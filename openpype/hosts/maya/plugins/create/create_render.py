# -*- coding: utf-8 -*-
"""Create ``Render`` instance in Maya."""
import os
import json
import appdirs
import requests
import six
import sys

from maya import cmds
import maya.app.renderSetup.model.renderSetup as renderSetup

from openpype.hosts.maya.api import (
    lib,
    plugin
)
from openpype.api import (
    get_system_settings,
    get_project_settings,
    get_asset)
from openpype.modules import ModulesManager

from avalon.api import Session
from avalon.api import CreatorError


class CreateRender(plugin.Creator):
    """Create *render* instance.

    Render instances are not actually published, they hold options for
    collecting of render data. It render instance is present, it will trigger
    collection of render layers, AOVs, cameras for either direct submission
    to render farm or export as various standalone formats (like V-Rays
    ``vrscenes`` or Arnolds ``ass`` files) and then submitting them to render
    farm.

    Instance has following attributes::

        primaryPool (list of str): Primary list of slave machine pool to use.
        secondaryPool (list of str): Optional secondary list of slave pools.
        suspendPublishJob (bool): Suspend the job after it is submitted.
        extendFrames (bool): Use already existing frames from previous version
            to extend current render.
        overrideExistingFrame (bool): Overwrite already existing frames.
        priority (int): Submitted job priority
        framesPerTask (int): How many frames per task to render. This is
            basically job division on render farm.
        whitelist (list of str): White list of slave machines
        machineList (list of str): Specific list of slave machines to use
        useMayaBatch (bool): Use Maya batch mode to render as opposite to
            Maya interactive mode. This consumes different licenses.
        vrscene (bool): Submit as ``vrscene`` file for standalone V-Ray
            renderer.
        ass (bool): Submit as ``ass`` file for standalone Arnold renderer.
        tileRendering (bool): Instance is set to tile rendering mode. We
            won't submit actuall render, but we'll make publish job to wait
            for Tile Assemly job done and then publish.

    See Also:
        https://pype.club/docs/artist_hosts_maya#creating-basic-render-setup

    """

    label = "Render"
    family = "rendering"
    icon = "eye"
    defaults = ["Main"]

    _token = None
    _user = None
    _password = None

    # renderSetup instance
    _rs = None

    _image_prefix_nodes = {
        'mentalray': 'defaultRenderGlobals.imageFilePrefix',
        'vray': 'vraySettings.fileNamePrefix',
        'arnold': 'defaultRenderGlobals.imageFilePrefix',
        'renderman': 'defaultRenderGlobals.imageFilePrefix',
        'redshift': 'defaultRenderGlobals.imageFilePrefix'
    }

    _image_prefixes = {
        'mentalray': 'maya/<Scene>/<RenderLayer>/<RenderLayer>{aov_separator}<RenderPass>',  # noqa
        'vray': 'maya/<scene>/<Layer>/<Layer>',
        'arnold': 'maya/<Scene>/<RenderLayer>/<RenderLayer>{aov_separator}<RenderPass>',  # noqa
        'renderman': 'maya/<Scene>/<layer>/<layer>{aov_separator}<aov>',
        'redshift': 'maya/<Scene>/<RenderLayer>/<RenderLayer>{aov_separator}<RenderPass>'  # noqa
    }

    _aov_chars = {
        "dot": ".",
        "dash": "-",
        "underscore": "_"
    }

    _project_settings = None

    def __init__(self, *args, **kwargs):
        """Constructor."""
        super(CreateRender, self).__init__(*args, **kwargs)
        deadline_settings = get_system_settings()["modules"]["deadline"]
        if not deadline_settings["enabled"]:
            self.deadline_servers = {}
            return
        self._project_settings = get_project_settings(
            Session["AVALON_PROJECT"])

        # project_settings/maya/create/CreateRender/aov_separator
        try:
            self.aov_separator = self._aov_chars[(
                self._project_settings["maya"]
                                      ["create"]
                                      ["CreateRender"]
                                      ["aov_separator"]
            )]
        except KeyError:
            self.aov_separator = "_"

        try:
            default_servers = deadline_settings["deadline_urls"]
            project_servers = (
                self._project_settings["deadline"]["deadline_servers"]
            )
            self.deadline_servers = {
                k: default_servers[k]
                for k in project_servers
                if k in default_servers
            }

            if not self.deadline_servers:
                self.deadline_servers = default_servers

        except AttributeError:
            # Handle situation were we had only one url for deadline.
            manager = ModulesManager()
            deadline_module = manager.modules_by_name["deadline"]
            # get default deadline webservice url from deadline module
            self.deadline_servers = deadline_module.deadline_urls

    def process(self):
        """Entry point."""
        exists = cmds.ls(self.name)
        if exists:
            cmds.warning("%s already exists." % exists[0])
            return

        use_selection = self.options.get("useSelection")
        with lib.undo_chunk():
            self._create_render_settings()
            self.instance = super(CreateRender, self).process()
            # create namespace with instance
            index = 1
            namespace_name = "_{}".format(str(self.instance))
            try:
                cmds.namespace(rm=namespace_name)
            except RuntimeError:
                # namespace is not empty, so we leave it untouched
                pass

            while cmds.namespace(exists=namespace_name):
                namespace_name = "_{}{}".format(str(self.instance), index)
                index += 1

            namespace = cmds.namespace(add=namespace_name)

            # add Deadline server selection list
            if self.deadline_servers:
                cmds.scriptJob(
                    attributeChange=[
                        "{}.deadlineServers".format(self.instance),
                        self._deadline_webservice_changed
                    ])

            cmds.setAttr("{}.machineList".format(self.instance), lock=True)
            self._rs = renderSetup.instance()
            layers = self._rs.getRenderLayers()
            if use_selection:
                print(">>> processing existing layers")
                sets = []
                for layer in layers:
                    print("  - creating set for {}:{}".format(
                        namespace, layer.name()))
                    render_set = cmds.sets(
                        n="{}:{}".format(namespace, layer.name()))
                    sets.append(render_set)
                cmds.sets(sets, forceElement=self.instance)

            # if no render layers are present, create default one with
            # asterisk selector
            if not layers:
                render_layer = self._rs.createRenderLayer('Main')
                collection = render_layer.createCollection("defaultCollection")
                collection.getSelector().setPattern('*')

            renderer = cmds.getAttr(
                'defaultRenderGlobals.currentRenderer').lower()
            # handle various renderman names
            if renderer.startswith('renderman'):
                renderer = 'renderman'

            self._set_default_renderer_settings(renderer)
        return self.instance

    def _deadline_webservice_changed(self):
        """Refresh Deadline server dependent options."""
        # get selected server
        from maya import cmds
        webservice = self.deadline_servers[
            self.server_aliases[
                cmds.getAttr("{}.deadlineServers".format(self.instance))
            ]
        ]
        pools = self._get_deadline_pools(webservice)
        cmds.deleteAttr("{}.primaryPool".format(self.instance))
        cmds.deleteAttr("{}.secondaryPool".format(self.instance))
        cmds.addAttr(self.instance, longName="primaryPool",
                     attributeType="enum",
                     enumName=":".join(pools))
        cmds.addAttr(self.instance, longName="secondaryPool",
                     attributeType="enum",
                     enumName=":".join(["-"] + pools))

    def _get_deadline_pools(self, webservice):
        # type: (str) -> list
        """Get pools from Deadline.
        Args:
            webservice (str): Server url.
        Returns:
            list: Pools.
        Throws:
            RuntimeError: If deadline webservice is unreachable.

        """
        argument = "{}/api/pools?NamesOnly=true".format(webservice)
        try:
            response = self._requests_get(argument)
        except requests.exceptions.ConnectionError as exc:
            msg = 'Cannot connect to deadline web service'
            self.log.error(msg)
            six.reraise(
                RuntimeError,
                RuntimeError('{} - {}'.format(msg, exc)),
                sys.exc_info()[2])
        if not response.ok:
            self.log.warning("No pools retrieved")
            return []

        return response.json()

    def _create_render_settings(self):
        """Create instance settings."""
        # get pools
        pool_names = []

        self.server_aliases = self.deadline_servers.keys()
        self.data["deadlineServers"] = self.server_aliases
        self.data["suspendPublishJob"] = False
        self.data["review"] = True
        self.data["extendFrames"] = False
        self.data["overrideExistingFrame"] = True
        # self.data["useLegacyRenderLayers"] = True
        self.data["priority"] = 50
        self.data["framesPerTask"] = 1
        self.data["whitelist"] = False
        self.data["machineList"] = ""
        self.data["useMayaBatch"] = False
        self.data["tileRendering"] = False
        self.data["tilesX"] = 2
        self.data["tilesY"] = 2
        self.data["convertToScanline"] = False
        self.data["useReferencedAovs"] = False
        # Disable for now as this feature is not working yet
        # self.data["assScene"] = False

        system_settings = get_system_settings()["modules"]

        deadline_enabled = system_settings["deadline"]["enabled"]
        muster_enabled = system_settings["muster"]["enabled"]
        muster_url = system_settings["muster"]["MUSTER_REST_URL"]

        if deadline_enabled and muster_enabled:
            self.log.error(
                "Both Deadline and Muster are enabled. " "Cannot support both."
            )
            raise RuntimeError("Both Deadline and Muster are enabled")

        if deadline_enabled:
            # if default server is not between selected, use first one for
            # initial list of pools.
            try:
                deadline_url = self.deadline_servers["default"]
            except KeyError:
                deadline_url = [
                    self.deadline_servers[k]
                    for k in self.deadline_servers.keys()
                ][0]

            pool_names = self._get_deadline_pools(deadline_url)

        if muster_enabled:
            self.log.info(">>> Loading Muster credentials ...")
            self._load_credentials()
            self.log.info(">>> Getting pools ...")
            pools = []
            try:
                pools = self._get_muster_pools()
            except requests.exceptions.HTTPError as e:
                if e.startswith("401"):
                    self.log.warning("access token expired")
                    self._show_login()
                    raise RuntimeError("Access token expired")
            except requests.exceptions.ConnectionError:
                self.log.error("Cannot connect to Muster API endpoint.")
                raise RuntimeError("Cannot connect to {}".format(muster_url))
            for pool in pools:
                self.log.info("  - pool: {}".format(pool["name"]))
                pool_names.append(pool["name"])

        self.data["primaryPool"] = pool_names
        # We add a string "-" to allow the user to not
        # set any secondary pools
        self.data["secondaryPool"] = ["-"] + pool_names
        self.options = {"useSelection": False}  # Force no content

    def _load_credentials(self):
        """Load Muster credentials.

        Load Muster credentials from file and set ``MUSTER_USER``,
        ``MUSTER_PASSWORD``, ``MUSTER_REST_URL`` is loaded from settings.

        Raises:
            RuntimeError: If loaded credentials are invalid.
            AttributeError: If ``MUSTER_REST_URL`` is not set.

        """
        app_dir = os.path.normpath(appdirs.user_data_dir("pype-app", "pype"))
        file_name = "muster_cred.json"
        fpath = os.path.join(app_dir, file_name)
        file = open(fpath, "r")
        muster_json = json.load(file)
        self._token = muster_json.get("token", None)
        if not self._token:
            self._show_login()
            raise RuntimeError("Invalid access token for Muster")
        file.close()
        self.MUSTER_REST_URL = os.environ.get("MUSTER_REST_URL")
        if not self.MUSTER_REST_URL:
            raise AttributeError("Muster REST API url not set")

    def _get_muster_pools(self):
        """Get render pools from Muster.

        Raises:
            Exception: If pool list cannot be obtained from Muster.

        """
        params = {"authToken": self._token}
        api_entry = "/api/pools/list"
        response = self._requests_get(self.MUSTER_REST_URL + api_entry,
                                      params=params)
        if response.status_code != 200:
            if response.status_code == 401:
                self.log.warning("Authentication token expired.")
                self._show_login()
            else:
                self.log.error(
                    ("Cannot get pools from "
                     "Muster: {}").format(response.status_code)
                )
                raise Exception("Cannot get pools from Muster")
        try:
            pools = response.json()["ResponseData"]["pools"]
        except ValueError as e:
            self.log.error("Invalid response from Muster server {}".format(e))
            raise Exception("Invalid response from Muster server")

        return pools

    def _show_login(self):
        # authentication token expired so we need to login to Muster
        # again to get it. We use Pype API call to show login window.
        api_url = "{}/muster/show_login".format(
            os.environ["OPENPYPE_WEBSERVER_URL"])
        self.log.debug(api_url)
        login_response = self._requests_get(api_url, timeout=1)
        if login_response.status_code != 200:
            self.log.error("Cannot show login form to Muster")
            raise Exception("Cannot show login form to Muster")

    def _requests_post(self, *args, **kwargs):
        """Wrap request post method.

        Disabling SSL certificate validation if ``DONT_VERIFY_SSL`` environment
        variable is found. This is useful when Deadline or Muster server are
        running with self-signed certificates and their certificate is not
        added to trusted certificates on client machines.

        Warning:
            Disabling SSL certificate validation is defeating one line
            of defense SSL is providing and it is not recommended.

        """
        if "verify" not in kwargs:
            kwargs["verify"] = not os.getenv("OPENPYPE_DONT_VERIFY_SSL", True)
        return requests.post(*args, **kwargs)

    def _requests_get(self, *args, **kwargs):
        """Wrap request get method.

        Disabling SSL certificate validation if ``DONT_VERIFY_SSL`` environment
        variable is found. This is useful when Deadline or Muster server are
        running with self-signed certificates and their certificate is not
        added to trusted certificates on client machines.

        Warning:
            Disabling SSL certificate validation is defeating one line
            of defense SSL is providing and it is not recommended.

        """
        if "verify" not in kwargs:
            kwargs["verify"] = not os.getenv("OPENPYPE_DONT_VERIFY_SSL", True)
        return requests.get(*args, **kwargs)

    def _set_default_renderer_settings(self, renderer):
        """Set basic settings based on renderer.

        Args:
            renderer (str): Renderer name.

        """
        prefix = self._image_prefixes[renderer]
        prefix = prefix.replace("{aov_separator}", self.aov_separator)
        cmds.setAttr(self._image_prefix_nodes[renderer],
                     prefix,
                     type="string")

        asset = get_asset()

        if renderer == "arnold":
            # set format to exr

            cmds.setAttr(
                "defaultArnoldDriver.ai_translator", "exr", type="string")
            self._set_global_output_settings()
            # resolution
            cmds.setAttr(
                "defaultResolution.width",
                asset["data"].get("resolutionWidth"))
            cmds.setAttr(
                "defaultResolution.height",
                asset["data"].get("resolutionHeight"))

        if renderer == "vray":
            self._set_vray_settings(asset)
        if renderer == "redshift":
            _ = self._set_renderer_option(
                "RedshiftOptions", "{}.imageFormat", 1
            )

            # resolution
            cmds.setAttr(
                "defaultResolution.width",
                asset["data"].get("resolutionWidth"))
            cmds.setAttr(
                "defaultResolution.height",
                asset["data"].get("resolutionHeight"))

            self._set_global_output_settings()

    def _set_vray_settings(self, asset):
        # type: (dict) -> None
        """Sets important settings for Vray."""
        settings = cmds.ls(type="VRaySettingsNode")
        node = settings[0] if settings else cmds.createNode("VRaySettingsNode")

        # set separator
        # set it in vray menu
        if cmds.optionMenuGrp("vrayRenderElementSeparator", exists=True,
                              q=True):
            items = cmds.optionMenuGrp(
                "vrayRenderElementSeparator", ill=True, query=True)

            separators = [cmds.menuItem(i, label=True, query=True) for i in items]  # noqa: E501
            try:
                sep_idx = separators.index(self.aov_separator)
            except ValueError:
                raise CreatorError(
                    "AOV character {} not in {}".format(
                        self.aov_separator, separators))

            cmds.optionMenuGrp(
                "vrayRenderElementSeparator", sl=sep_idx + 1, edit=True)
        cmds.setAttr(
            "{}.fileNameRenderElementSeparator".format(node),
            self.aov_separator,
            type="string"
        )
        # set format to exr
        cmds.setAttr(
            "{}.imageFormatStr".format(node), "exr", type="string")

        # animType
        cmds.setAttr(
            "{}.animType".format(node), 1)

        # resolution
        cmds.setAttr(
            "{}.width".format(node),
            asset["data"].get("resolutionWidth"))
        cmds.setAttr(
            "{}.height".format(node),
            asset["data"].get("resolutionHeight"))

    @staticmethod
    def _set_global_output_settings():
        # enable animation
        cmds.setAttr("defaultRenderGlobals.outFormatControl", 0)
        cmds.setAttr("defaultRenderGlobals.animation", 1)
        cmds.setAttr("defaultRenderGlobals.putFrameBeforeExt", 1)
        cmds.setAttr("defaultRenderGlobals.extensionPadding", 4)
