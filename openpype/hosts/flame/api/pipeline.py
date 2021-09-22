"""
Basic avalon integration
"""
import os
import contextlib
from collections import OrderedDict
from avalon.tools import workfiles
from avalon import api as avalon
from avalon import schema
from avalon.pipeline import AVALON_CONTAINER_ID
from pyblish import api as pyblish
from openpype.api import Logger
from . import lib
from . import PLUGINS_DIR

log = Logger().get_logger(__name__)

PUBLISH_PATH = os.path.join(PLUGINS_DIR, "publish")
LOAD_PATH = os.path.join(PLUGINS_DIR, "load")
CREATE_PATH = os.path.join(PLUGINS_DIR, "create")
INVENTORY_PATH = os.path.join(PLUGINS_DIR, "inventory")

AVALON_CONTAINERS = ":AVALON_CONTAINERS"


def install():
    # TODO: install

    # Disable all families except for the ones we explicitly want to see
    family_states = [
        "imagesequence",
        "render2d",
        "plate",
        "render",
        "mov",
        "clip"
    ]
    avalon.data["familiesStateDefault"] = False
    avalon.data["familiesStateToggled"] = family_states

    log.info("openpype.hosts.flame installed")

    pyblish.register_host("flame")
    pyblish.register_plugin_path(PUBLISH_PATH)
    log.info("Registering DaVinci Resovle plug-ins..")

    avalon.register_plugin_path(avalon.Loader, LOAD_PATH)
    avalon.register_plugin_path(avalon.Creator, CREATE_PATH)
    avalon.register_plugin_path(avalon.InventoryAction, INVENTORY_PATH)

    # register callback for switching publishable
    pyblish.register_callback("instanceToggled", on_pyblish_instance_toggled)


def uninstall():
    # TODO: uninstall
    pyblish.deregister_host("flame")
    pyblish.deregister_plugin_path(PUBLISH_PATH)
    log.info("Deregistering DaVinci Resovle plug-ins..")

    avalon.deregister_plugin_path(avalon.Loader, LOAD_PATH)
    avalon.deregister_plugin_path(avalon.Creator, CREATE_PATH)
    avalon.deregister_plugin_path(avalon.InventoryAction, INVENTORY_PATH)

    # register callback for switching publishable
    pyblish.deregister_callback("instanceToggled", on_pyblish_instance_toggled)


def containerise(tl_segment,
                 name,
                 namespace,
                 context,
                 loader=None,
                 data=None):
    # TODO: containerise
    pass


def ls():
    """List available containers.
    """
    # TODO: ls
    pass


def parse_container(tl_segment, validate=True):
    """Return container data from timeline_item's openpype tag.
    """
    # TODO: parse_container
    pass


def update_container(tl_segment, data=None):
    """Update container data to input timeline_item's openpype tag.
    """
    # TODO: update_container
    pass

@contextlib.contextmanager
def maintained_selection():
    """Maintain selection during context

    Example:
        >>> with maintained_selection():
        ...     node['selected'].setValue(True)
        >>> print(node['selected'].value())
        False
    """
    # TODO: maintained_selection + remove undo steps

    try:
        # do the operation
        yield
    finally:
        pass


def reset_selection():
    """Deselect all selected nodes
    """
    pass


def on_pyblish_instance_toggled(instance, old_value, new_value):
    """Toggle node passthrough states on instance toggles."""

    log.info("instance toggle: {}, old_value: {}, new_value:{} ".format(
        instance, old_value, new_value))

    from openpype.hosts.resolve import (
        set_publish_attribute
    )

    # Whether instances should be passthrough based on new value
    timeline_item = instance.data["item"]
    set_publish_attribute(timeline_item, new_value)


def remove_instance(instance):
    """Remove instance marker from track item."""
    # TODO: remove_instance
    pass


def list_instances():
    """List all created instances from current workfile."""
    # TODO: list_instances
    pass


def imprint(item, data=None):
    # TODO: imprint
    pass
