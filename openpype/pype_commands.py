# -*- coding: utf-8 -*-
"""Implementation of Pype commands."""
import os
import sys
import json
import time

from openpype.lib import PypeLogger
from openpype.api import get_app_environments_for_context
from openpype.lib.plugin_tools import parse_json, get_batch_asset_task_info
from openpype.lib.remote_publish import (
    get_webpublish_conn,
    start_webpublish_log,
    publish_and_log,
    fail_batch,
    find_variant_key,
    get_task_data
)


class PypeCommands:
    """Class implementing commands used by Pype.

    Most of its methods are called by :mod:`cli` module.
    """
    @staticmethod
    def launch_tray(debug=False):
        PypeLogger.set_process_name("Tray")

        from openpype.tools import tray

        tray.main()

    @staticmethod
    def launch_settings_gui(dev):
        from openpype.tools import settings

        # TODO change argument options to allow enum of user roles
        if dev:
            user_role = "developer"
        else:
            user_role = "manager"
        settings.main(user_role)

    @staticmethod
    def add_modules(click_func):
        """Modules/Addons can add their cli commands dynamically."""
        from openpype.modules import ModulesManager

        manager = ModulesManager()
        log = PypeLogger.get_logger("AddModulesCLI")
        for module in manager.modules:
            try:
                module.cli(click_func)

            except Exception:
                log.warning(
                    "Failed to add cli command for module \"{}\"".format(
                        module.name
                    )
                )
        return click_func

    @staticmethod
    def launch_eventservercli(*args):
        from openpype_modules.ftrack.ftrack_server.event_server_cli import (
            run_event_server
        )
        return run_event_server(*args)

    @staticmethod
    def launch_webpublisher_webservercli(*args, **kwargs):
        from openpype.hosts.webpublisher.webserver_service.webserver_cli \
            import (run_webserver)
        return run_webserver(*args, **kwargs)

    @staticmethod
    def launch_standalone_publisher():
        from openpype.tools import standalonepublish
        standalonepublish.main()

    @staticmethod
    def publish(paths, targets=None, gui=False):
        """Start headless publishing.

        Publish use json from passed paths argument.

        Args:
            paths (list): Paths to jsons.
            targets (string): What module should be targeted
                (to choose validator for example)
            gui (bool): Show publish UI.

        Raises:
            RuntimeError: When there is no path to process.
        """
        from openpype.modules import ModulesManager
        from openpype import install, uninstall
        from openpype.api import Logger
        from openpype.tools.utils.host_tools import show_publish
        from openpype.tools.utils.lib import qt_app_context

        # Register target and host
        import pyblish.api
        import pyblish.util

        log = Logger.get_logger()

        install()

        manager = ModulesManager()

        publish_paths = manager.collect_plugin_paths()["publish"]

        for path in publish_paths:
            pyblish.api.register_plugin_path(path)

        if not any(paths):
            raise RuntimeError("No publish paths specified")

        env = get_app_environments_for_context(
            os.environ["AVALON_PROJECT"],
            os.environ["AVALON_ASSET"],
            os.environ["AVALON_TASK"],
            os.environ["AVALON_APP_NAME"]
        )
        os.environ.update(env)

        pyblish.api.register_host("shell")

        if targets:
            for target in targets:
                print(f"setting target: {target}")
                pyblish.api.register_target(target)
        else:
            pyblish.api.register_target("filesequence")

        os.environ["OPENPYPE_PUBLISH_DATA"] = os.pathsep.join(paths)

        log.info("Running publish ...")

        plugins = pyblish.api.discover()
        print("Using plugins:")
        for plugin in plugins:
            print(plugin)

        if gui:
            with qt_app_context():
                show_publish()
        else:
            # Error exit as soon as any error occurs.
            error_format = ("Failed {plugin.__name__}: "
                            "{error} -- {error.traceback}")

            for result in pyblish.util.publish_iter():
                if result["error"]:
                    log.error(error_format.format(**result))
                    # uninstall()
                    sys.exit(1)

        log.info("Publish finished.")

    @staticmethod
    def remotepublishfromapp(project, batch_dir, host_name,
                             user, targets=None):
        """Opens installed variant of 'host' and run remote publish there.

            Currently implemented and tested for Photoshop where customer
            wants to process uploaded .psd file and publish collected layers
            from there.

            Checks if no other batches are running (status =='in_progress). If
            so, it sleeps for SLEEP (this is separate process),
            waits for WAIT_FOR seconds altogether.

            Requires installed host application on the machine.

            Runs publish process as user would, in automatic fashion.
        """
        import pyblish.api
        from openpype.api import Logger
        from openpype.lib import ApplicationManager

        log = Logger.get_logger()

        log.info("remotepublishphotoshop command")

        task_data = get_task_data(batch_dir)

        workfile_path = os.path.join(batch_dir,
                                     task_data["task"],
                                     task_data["files"][0])

        print("workfile_path {}".format(workfile_path))

        batch_id = task_data["batch"]
        dbcon = get_webpublish_conn()
        # safer to start logging here, launch might be broken altogether
        _id = start_webpublish_log(dbcon, batch_id, user)

        batches_in_progress = list(dbcon.find({"status": "in_progress"}))
        if len(batches_in_progress) > 1:
            fail_batch(_id, batches_in_progress, dbcon)
            print("Another batch running, probably stuck, ask admin for help")

        asset, task_name, _ = get_batch_asset_task_info(task_data["context"])

        application_manager = ApplicationManager()
        found_variant_key = find_variant_key(application_manager, host_name)
        app_name = "{}/{}".format(host_name, found_variant_key)

        # must have for proper launch of app
        env = get_app_environments_for_context(
            project,
            asset,
            task_name,
            app_name
        )
        os.environ.update(env)

        os.environ["OPENPYPE_PUBLISH_DATA"] = batch_dir
        # must pass identifier to update log lines for a batch
        os.environ["BATCH_LOG_ID"] = str(_id)
        os.environ["HEADLESS_PUBLISH"] = 'true'  # to use in app lib

        pyblish.api.register_host(host_name)
        if targets:
            if isinstance(targets, str):
                targets = [targets]
            current_targets = os.environ.get("PYBLISH_TARGETS", "").split(
                os.pathsep)
            for target in targets:
                current_targets.append(target)

            os.environ["PYBLISH_TARGETS"] = os.pathsep.join(
                set(current_targets))

        data = {
            "last_workfile_path": workfile_path,
            "start_last_workfile": True
        }

        launched_app = application_manager.launch(app_name, **data)

        while launched_app.poll() is None:
            time.sleep(0.5)

    @staticmethod
    def remotepublish(project, batch_path, user, targets=None):
        """Start headless publishing.

        Used to publish rendered assets, workfiles etc.

        Publish use json from passed paths argument.

        Args:
            project (str): project to publish (only single context is expected
                per call of remotepublish
            batch_path (str): Path batch folder. Contains subfolders with
                resources (workfile, another subfolder 'renders' etc.)
            user (string): email address for webpublisher
            targets (list): Pyblish targets
                (to choose validator for example)

        Raises:
            RuntimeError: When there is no path to process.
        """
        if not batch_path:
            raise RuntimeError("No publish paths specified")

        # Register target and host
        import pyblish.api
        import pyblish.util
        import avalon.api
        from openpype.hosts.webpublisher import api as webpublisher

        log = PypeLogger.get_logger()

        log.info("remotepublish command")

        host_name = "webpublisher"
        os.environ["OPENPYPE_PUBLISH_DATA"] = batch_path
        os.environ["AVALON_PROJECT"] = project
        os.environ["AVALON_APP"] = host_name

        pyblish.api.register_host(host_name)

        if targets:
            if isinstance(targets, str):
                targets = [targets]
            for target in targets:
                pyblish.api.register_target(target)

        avalon.api.install(webpublisher)

        log.info("Running publish ...")

        _, batch_id = os.path.split(batch_path)
        dbcon = get_webpublish_conn()
        _id = start_webpublish_log(dbcon, batch_id, user)

        publish_and_log(dbcon, _id, log)

        log.info("Publish finished.")

    @staticmethod
    def extractenvironments(output_json_path, project, asset, task, app):
        env = os.environ.copy()
        if all((project, asset, task, app)):
            from openpype.api import get_app_environments_for_context
            env = get_app_environments_for_context(
                project, asset, task, app, env
            )

        output_dir = os.path.dirname(output_json_path)
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        with open(output_json_path, "w") as file_stream:
            json.dump(env, file_stream, indent=4)

    @staticmethod
    def launch_project_manager():
        from openpype.tools import project_manager

        project_manager.main()

    @staticmethod
    def contextselection(output_path, project_name, asset_name, strict):
        from openpype.tools.context_dialog import main

        main(output_path, project_name, asset_name, strict)

    def texture_copy(self, project, asset, path):
        pass

    def run_application(self, app, project, asset, task, tools, arguments):
        pass

    def validate_jsons(self):
        pass

    def run_tests(self, folder, mark, pyargs):
        """
            Runs tests from 'folder'

            Args:
                 folder (str): relative path to folder with tests
                 mark (str): label to run tests marked by it (slow etc)
                 pyargs (str): package path to test
        """
        print("run_tests")
        import subprocess

        if folder:
            folder = " ".join(list(folder))
        else:
            folder = "../tests"

        mark_str = pyargs_str = ''
        if mark:
            mark_str = "-m {}".format(mark)

        if pyargs:
            pyargs_str = "--pyargs {}".format(pyargs)

        cmd = "pytest {} {} {}".format(folder, mark_str, pyargs_str)
        print("Running {}".format(cmd))
        subprocess.run(cmd)

    def syncserver(self, active_site):
        """Start running sync_server in background."""
        import signal
        os.environ["OPENPYPE_LOCAL_ID"] = active_site

        def signal_handler(sig, frame):
            print("You pressed Ctrl+C. Process ended.")
            sync_server_module.server_exit()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        from openpype.modules import ModulesManager

        manager = ModulesManager()
        sync_server_module = manager.modules_by_name["sync_server"]

        sync_server_module.server_init()
        sync_server_module.server_start()

        import time
        while True:
            time.sleep(1.0)

    def repack_version(self, directory):
        """Repacking OpenPype version."""
        from openpype.tools.repack_version import VersionRepacker

        version_packer = VersionRepacker(directory)
        version_packer.process()
