import os
import json
import datetime
import platform
import getpass
import socket

import openpype.version
from openpype.settings.lib import get_local_settings
from .execute import get_pype_execute_args
from .local_settings import get_local_site_id
from .python_module_tools import import_filepath


def get_openpype_version():
    """Version of pype that is currently used."""
    return openpype.version.__version__


def get_pype_version():
    """Backwards compatibility. Remove when 100% not used."""
    print((
        "Using deprecated function 'openpype.lib.pype_info.get_pype_version'"
        " replace with 'openpype.lib.pype_info.get_openpype_version'."
    ))
    return get_openpype_version()


def get_build_version():
    """OpenPype version of build."""
    # Return OpenPype version if is running from code
    if not is_running_from_build():
        return get_openpype_version()

    # Import `version.py` from build directory
    version_filepath = os.path.join(
        os.environ["OPENPYPE_ROOT"],
        "openpype",
        "version.py"
    )
    if not os.path.exists(version_filepath):
        return None

    module = import_filepath(version_filepath, "openpype_build_version")
    return getattr(module, "__version__", None)


def is_running_from_build():
    """Determine if current process is running from build or code.

    Returns:
        bool: True if running from build.
    """
    executable_path = os.environ["OPENPYPE_EXECUTABLE"]
    executable_filename = os.path.basename(executable_path)
    if "python" in executable_filename.lower():
        return False
    return True


def is_running_staging():
    """Currently used OpenPype is staging version.

    Returns:
        bool: True if openpype version containt 'staging'.
    """
    if "staging" in get_openpype_version():
        return True
    return False


def get_pype_info():
    """Information about currently used Pype process."""
    executable_args = get_pype_execute_args()
    if is_running_from_build():
        version_type = "build"
    else:
        version_type = "code"

    return {
        "version": get_openpype_version(),
        "version_type": version_type,
        "executable": executable_args[-1],
        "pype_root": os.environ["OPENPYPE_REPOS_ROOT"],
        "mongo_url": os.environ["OPENPYPE_MONGO"]
    }


def get_workstation_info():
    """Basic information about workstation."""
    host_name = socket.gethostname()
    try:
        host_ip = socket.gethostbyname(host_name)
    except socket.gaierror:
        host_ip = "127.0.0.1"

    return {
        "hostname": host_name,
        "hostip": host_ip,
        "username": getpass.getuser(),
        "system_name": platform.system(),
        "local_id": get_local_site_id()
    }


def get_all_current_info():
    """All information about current process in one dictionary."""
    return {
        "pype": get_pype_info(),
        "workstation": get_workstation_info(),
        "env": os.environ.copy(),
        "local_settings": get_local_settings()
    }


def extract_pype_info_to_file(dirpath):
    """Extract all current info to a file.

    It is possible to define onpy directory path. Filename is concatenated with
    pype version, workstation site id and timestamp.

    Args:
        dirpath (str): Path to directory where file will be stored.

    Returns:
        filepath (str): Full path to file where data were extracted.
    """
    filename = "{}_{}_{}.json".format(
        get_openpype_version(),
        get_local_site_id(),
        datetime.datetime.now().strftime("%y%m%d%H%M%S")
    )
    filepath = os.path.join(dirpath, filename)
    data = get_all_current_info()
    if not os.path.exists(dirpath):
        os.makedirs(dirpath)

    with open(filepath, "w") as file_stream:
        json.dump(data, file_stream, indent=4)
    return filepath
