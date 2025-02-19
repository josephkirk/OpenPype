import os
import sys
import contextlib
import collections

from Qt import QtWidgets, QtCore, QtGui

import avalon.api
from avalon import style
from avalon.vendor import qtawesome

from openpype.api import get_project_settings
from openpype.lib import filter_profiles


def center_window(window):
    """Move window to center of it's screen."""
    desktop = QtWidgets.QApplication.desktop()
    screen_idx = desktop.screenNumber(window)
    screen_geo = desktop.screenGeometry(screen_idx)
    geo = window.frameGeometry()
    geo.moveCenter(screen_geo.center())
    if geo.y() < screen_geo.y():
        geo.setY(screen_geo.y())
    window.move(geo.topLeft())


def format_version(value, hero_version=False):
    """Formats integer to displayable version name"""
    label = "v{0:03d}".format(value)
    if not hero_version:
        return label
    return "[{}]".format(label)


@contextlib.contextmanager
def qt_app_context():
    app = QtWidgets.QApplication.instance()

    if not app:
        print("Starting new QApplication..")
        app = QtWidgets.QApplication(sys.argv)
        yield app
        app.exec_()
    else:
        print("Using existing QApplication..")
        yield app


# Backwards compatibility
application = qt_app_context


class SharedObjects:
    jobs = {}


def schedule(func, time, channel="default"):
    """Run `func` at a later `time` in a dedicated `channel`

    Given an arbitrary function, call this function after a given
    timeout. It will ensure that only one "job" is running within
    the given channel at any one time and cancel any currently
    running job if a new job is submitted before the timeout.

    """

    try:
        SharedObjects.jobs[channel].stop()
    except (AttributeError, KeyError, RuntimeError):
        pass

    timer = QtCore.QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(func)
    timer.start(time)

    SharedObjects.jobs[channel] = timer


def iter_model_rows(model, column, include_root=False):
    """Iterate over all row indices in a model"""
    indices = [QtCore.QModelIndex()]  # start iteration at root

    for index in indices:
        # Add children to the iterations
        child_rows = model.rowCount(index)
        for child_row in range(child_rows):
            child_index = model.index(child_row, column, index)
            indices.append(child_index)

        if not include_root and not index.isValid():
            continue

        yield index


@contextlib.contextmanager
def preserve_expanded_rows(tree_view, column=0, role=None):
    """Preserves expanded row in QTreeView by column's data role.

    This function is created to maintain the expand vs collapse status of
    the model items. When refresh is triggered the items which are expanded
    will stay expanded and vise versa.

    Arguments:
        tree_view (QWidgets.QTreeView): the tree view which is
            nested in the application
        column (int): the column to retrieve the data from
        role (int): the role which dictates what will be returned

    Returns:
        None

    """
    if role is None:
        role = QtCore.Qt.DisplayRole
    model = tree_view.model()

    expanded = set()

    for index in iter_model_rows(model, column=column, include_root=False):
        if tree_view.isExpanded(index):
            value = index.data(role)
            expanded.add(value)

    try:
        yield
    finally:
        if not expanded:
            return

        for index in iter_model_rows(model, column=column, include_root=False):
            value = index.data(role)
            state = value in expanded
            if state:
                tree_view.expand(index)
            else:
                tree_view.collapse(index)


@contextlib.contextmanager
def preserve_selection(tree_view, column=0, role=None, current_index=True):
    """Preserves row selection in QTreeView by column's data role.

    This function is created to maintain the selection status of
    the model items. When refresh is triggered the items which are expanded
    will stay expanded and vise versa.

        tree_view (QWidgets.QTreeView): the tree view nested in the application
        column (int): the column to retrieve the data from
        role (int): the role which dictates what will be returned

    Returns:
        None

    """
    if role is None:
        role = QtCore.Qt.DisplayRole
    model = tree_view.model()
    selection_model = tree_view.selectionModel()
    flags = selection_model.Select | selection_model.Rows

    if current_index:
        current_index_value = tree_view.currentIndex().data(role)
    else:
        current_index_value = None

    selected_rows = selection_model.selectedRows()
    if not selected_rows:
        yield
        return

    selected = set(row.data(role) for row in selected_rows)
    try:
        yield
    finally:
        if not selected:
            return

        # Go through all indices, select the ones with similar data
        for index in iter_model_rows(model, column=column, include_root=False):
            value = index.data(role)
            state = value in selected
            if state:
                tree_view.scrollTo(index)  # Ensure item is visible
                selection_model.select(index, flags)

            if current_index_value and value == current_index_value:
                selection_model.setCurrentIndex(
                    index, selection_model.NoUpdate
                )


class FamilyConfigCache:
    default_color = "#0091B2"
    _default_icon = None

    def __init__(self, dbcon):
        self.dbcon = dbcon
        self.family_configs = {}
        self._family_filters_set = False
        self._require_refresh = True

    @classmethod
    def default_icon(cls):
        if cls._default_icon is None:
            cls._default_icon = qtawesome.icon(
                "fa.folder", color=cls.default_color
            )
        return cls._default_icon

    def family_config(self, family_name):
        """Get value from config with fallback to default"""
        if self._require_refresh:
            self._refresh()

        item = self.family_configs.get(family_name)
        if not item:
            item = {
                "icon": self.default_icon()
            }
            if self._family_filters_set:
                item["state"] = False
        return item

    def refresh(self, force=False):
        self._require_refresh = True

        if force:
            self._refresh()

    def _refresh(self):
        """Get the family configurations from the database

        The configuration must be stored on the project under `config`.
        For example:

        {"config": {
            "families": [
                {"name": "avalon.camera", label: "Camera", "icon": "photo"},
                {"name": "avalon.anim", label: "Animation", "icon": "male"},
            ]
        }}

        It is possible to override the default behavior and set specific
        families checked. For example we only want the families imagesequence
        and camera to be visible in the Loader.
        """
        self._require_refresh = False
        self._family_filters_set = False

        self.family_configs.clear()
        # Skip if we're not in host context
        if not avalon.api.registered_host():
            return

        # Update the icons from the project configuration
        project_name = os.environ.get("AVALON_PROJECT")
        asset_name = os.environ.get("AVALON_ASSET")
        task_name = os.environ.get("AVALON_TASK")
        if not all((project_name, asset_name, task_name)):
            return

        matching_item = None
        project_settings = get_project_settings(project_name)
        profiles = (
            project_settings
            ["global"]
            ["tools"]
            ["loader"]
            ["family_filter_profiles"]
        )
        if profiles:
            asset_doc = self.dbcon.find_one(
                {"type": "asset", "name": asset_name},
                {"data.tasks": True}
            )
            tasks_info = asset_doc.get("data", {}).get("tasks") or {}
            task_type = tasks_info.get(task_name, {}).get("type")
            profiles_filter = {
                "task_types": task_type,
                "hosts": os.environ["AVALON_APP"]
            }
            matching_item = filter_profiles(profiles, profiles_filter)

        families = []
        if matching_item:
            families = matching_item["filter_families"]

        if not families:
            return

        self._family_filters_set = True

        # Replace icons with a Qt icon we can use in the user interfaces
        for family in families:
            family_info = {
                "name": family,
                "icon": self.default_icon(),
                "state": True
            }

            self.family_configs[family] = family_info


class GroupsConfig:
    # Subset group item's default icon and order
    _default_group_config = None

    def __init__(self, dbcon):
        self.dbcon = dbcon
        self.groups = {}

    @classmethod
    def default_group_config(cls):
        if cls._default_group_config is None:
            cls._default_group_config = {
                "icon": qtawesome.icon(
                    "fa.object-group",
                    color=style.colors.default
                ),
                "order": 0
            }
        return cls._default_group_config

    def refresh(self):
        """Get subset group configurations from the database

        The 'group' configuration must be stored in the project `config` field.
        See schema `config-1.0.json`

        """
        # Clear cached groups
        self.groups.clear()

        group_configs = []
        project_name = self.dbcon.Session.get("AVALON_PROJECT")
        if project_name:
            # Get pre-defined group name and apperance from project config
            project_doc = self.dbcon.find_one(
                {"type": "project"},
                projection={"config.groups": True}
            )

            if project_doc:
                group_configs = project_doc["config"].get("groups") or []
            else:
                print("Project not found! \"{}\"".format(project_name))

        # Build pre-defined group configs
        for config in group_configs:
            name = config["name"]
            icon = "fa." + config.get("icon", "object-group")
            color = config.get("color", style.colors.default)
            order = float(config.get("order", 0))

            self.groups[name] = {
                "icon": qtawesome.icon(icon, color=color),
                "order": order
            }

        return self.groups

    def ordered_groups(self, group_names):
        # default order zero included
        _orders = set([0])
        for config in self.groups.values():
            _orders.add(config["order"])

        # Remap order to list index
        orders = sorted(_orders)

        _groups = list()
        for name in group_names:
            # Get group config
            config = self.groups.get(name) or self.default_group_config()
            # Base order
            remapped_order = orders.index(config["order"])

            data = {
                "name": name,
                "icon": config["icon"],
                "_order": remapped_order,
            }

            _groups.append(data)

        # Sort by tuple (base_order, name)
        # If there are multiple groups in same order, will sorted by name.
        ordered_groups = sorted(
            _groups, key=lambda _group: (_group.pop("_order"), _group["name"])
        )

        total = len(ordered_groups)
        order_temp = "%0{}d".format(len(str(total)))

        # Update sorted order to config
        for index, group_data in enumerate(ordered_groups):
            order = index
            inverse_order = total - index

            # Format orders into fixed length string for groups sorting
            group_data["order"] = order_temp % order
            group_data["inverseOrder"] = order_temp % inverse_order

        return ordered_groups

    def active_groups(self, asset_ids, include_predefined=True):
        """Collect all active groups from each subset"""
        # Collect groups from subsets
        group_names = set(
            self.dbcon.distinct(
                "data.subsetGroup",
                {"type": "subset", "parent": {"$in": asset_ids}}
            )
        )
        if include_predefined:
            # Ensure all predefined group configs will be included
            group_names.update(self.groups.keys())

        return self.ordered_groups(group_names)

    def split_subsets_for_groups(self, subset_docs, grouping):
        """Collect all active groups from each subset"""
        subset_docs_without_group = collections.defaultdict(list)
        subset_docs_by_group = collections.defaultdict(dict)
        for subset_doc in subset_docs:
            subset_name = subset_doc["name"]
            if grouping:
                group_name = subset_doc["data"].get("subsetGroup")
                if group_name:
                    if subset_name not in subset_docs_by_group[group_name]:
                        subset_docs_by_group[group_name][subset_name] = []

                    subset_docs_by_group[group_name][subset_name].append(
                        subset_doc
                    )
                    continue

            subset_docs_without_group[subset_name].append(subset_doc)

        ordered_groups = self.ordered_groups(subset_docs_by_group.keys())

        return ordered_groups, subset_docs_without_group, subset_docs_by_group


class DynamicQThread(QtCore.QThread):
    """QThread which can run any function with argument and kwargs.

    Args:
        func (function): Function which will be called.
        args (tuple): Arguments which will be passed to function.
        kwargs (tuple): Keyword arguments which will be passed to function.
        parent (QObject): Parent of thread.
    """
    def __init__(self, func, args=None, kwargs=None, parent=None):
        super(DynamicQThread, self).__init__(parent)
        if args is None:
            args = tuple()
        if kwargs is None:
            kwargs = {}
        self._func = func
        self._args = args
        self._kwargs = kwargs

    def run(self):
        """Execute the function with arguments."""
        self._func(*self._args, **self._kwargs)


def create_qthread(func, *args, **kwargs):
    class Thread(QtCore.QThread):
        def run(self):
            func(*args, **kwargs)
    return Thread()


def get_repre_icons():
    try:
        from openpype_modules import sync_server
    except Exception:
        # Backwards compatibility
        from openpype.modules import sync_server

    resource_path = os.path.join(
        os.path.dirname(sync_server.sync_server_module.__file__),
        "providers", "resources"
    )
    icons = {}
    # TODO get from sync module
    for provider in ['studio', 'local_drive', 'gdrive']:
        pix_url = "{}/{}.png".format(resource_path, provider)
        icons[provider] = QtGui.QIcon(pix_url)

    return icons


def get_progress_for_repre(doc, active_site, remote_site):
    """
        Calculates average progress for representation.

        If site has created_dt >> fully available >> progress == 1

        Could be calculated in aggregate if it would be too slow
        Args:
            doc(dict): representation dict
        Returns:
            (dict) with active and remote sites progress
            {'studio': 1.0, 'gdrive': -1} - gdrive site is not present
                -1 is used to highlight the site should be added
            {'studio': 1.0, 'gdrive': 0.0} - gdrive site is present, not
                uploaded yet
    """
    progress = {active_site: -1,
                remote_site: -1}
    if not doc:
        return progress

    files = {active_site: 0, remote_site: 0}
    doc_files = doc.get("files") or []
    for doc_file in doc_files:
        if not isinstance(doc_file, dict):
            continue

        sites = doc_file.get("sites") or []
        for site in sites:
            if (
                # Pype 2 compatibility
                not isinstance(site, dict)
                # Check if site name is one of progress sites
                or site["name"] not in progress
            ):
                continue

            files[site["name"]] += 1
            norm_progress = max(progress[site["name"]], 0)
            if site.get("created_dt"):
                progress[site["name"]] = norm_progress + 1
            elif site.get("progress"):
                progress[site["name"]] = norm_progress + site["progress"]
            else:  # site exists, might be failed, do not add again
                progress[site["name"]] = 0

    # for example 13 fully avail. files out of 26 >> 13/26 = 0.5
    avg_progress = {}
    avg_progress[active_site] = \
        progress[active_site] / max(files[active_site], 1)
    avg_progress[remote_site] = \
        progress[remote_site] / max(files[remote_site], 1)
    return avg_progress


def is_sync_loader(loader):
    return is_remove_site_loader(loader) or is_add_site_loader(loader)


def is_remove_site_loader(loader):
    return hasattr(loader, "remove_site_on_representation")


def is_add_site_loader(loader):
    return hasattr(loader, "add_site_to_representation")
