import os
import shutil
import configparser
from argparse import Namespace

from .params import CENSORCNAME, ASSETS_PATH, USER_ASSETS_PATH
from .qm_processor import QmProc
from .utilities import DfaHelper, SolventHelper, print

parts = {}

# Flag to indicate wether a rcfile has been found in the home directory
homerc = None


def configure(rcpath: str = None, create_new: bool = False):
    """
    Configures the application based on the provided configuration file path.
    If no configuration file path is provided, it searches for the default configuration file.
    If no configuration file is found, it creates a new one with default settings.
    """
    # Try to find the .censo2rc in the user's home directory
    # if no configuration file path is provided
    if rcpath is None:
        censorc_path = find_rcfile()
    else:
        if not os.path.isfile(rcpath) and not create_new:
            raise FileNotFoundError(
                f"No configuration file found at {rcpath}.")
        else:
            censorc_path = rcpath

    # Set up the DFAHelper
    DfaHelper.set_dfa_dict(os.path.join(
        ASSETS_PATH, "censo_dfa_settings.json"))

    # Set up the SolventHelper
    SolventHelper.set_solvent_dict(os.path.join(
        ASSETS_PATH, "censo_solvents_db.json"))

    # map the part names to their respective classes
    # NOTE: the DFAHelper and the databases should be setup before the parts are imported,
    # otherwise there will be errors in the CensoPart._options
    from .part import CensoPart
    from .ensembleopt import Prescreening, Screening, Optimization, Refinement
    from .properties import NMR, UVVis

    global parts
    parts = {
        "prescreening": Prescreening,
        "screening": Screening,
        "optimization": Optimization,
        "refinement": Refinement,
        "nmr": NMR,
        "uvvis": UVVis,
    }

    # If no configuration file was found above, set the rcflag to False
    global homerc
    if censorc_path is None:
        homerc = False
        return
    # if explicitely told to create a new configuration file, do so
    elif create_new:
        censorc_path = os.path.join(rcpath, "censo2rc_NEW")
        write_rcfile(censorc_path)
    # Otherwise, read the configuration file and configure the parts with the settings from it
    else:
        # Initialize default settings
        # Make sure that settings are initialized even if there is no section for this part in the rcfile
        for part in parts.values():
            part.set_settings({})

        homerc = True
        settings_dict = read_rcfile(censorc_path)

        # first set general settings
        CensoPart.set_general_settings(settings_dict["general"])

        # set settings for each part
        for section, settings in settings_dict.items():
            if section in parts.keys():
                parts[section].set_settings(settings)
            # NOTE: if section is not in the parts names, it will be ignored

    # Update the paths for the processors
    paths = read_rcfile(censorc_path)["paths"]
    QmProc._paths.update(paths)

    # create user assets folder if it does not exist
    if not os.path.isdir(USER_ASSETS_PATH):
        os.mkdir(USER_ASSETS_PATH)


def read_rcfile(path: str) -> dict[str, dict[str, any]]:
    """
    Read from config data from file located at 'path'
    """
    # read config file
    parser: configparser.ConfigParser = configparser.ConfigParser()
    with open(path, "r") as file:
        parser.read_file(file)

    returndict = {section: dict(parser[section])
                  for section in parser.sections()}
    return returndict


def write_rcfile(path: str) -> None:
    """
    Write new configuration file with default settings into file at 'path'.
    Also reads program paths from preexisting configuration file or tries to 
    determine the paths automatically.

    Args:
        path (str): Path to the new configuration file.

    Returns:
        None
    """
    # what to do if there is an existing configuration file
    external_paths = None
    if os.path.isfile(path):
        print(
            f"An existing configuration file has been found at {path}.\n",
            f"Renaming existing file to {CENSORCNAME}_OLD.\n",
        )
        # Read program paths from the existing configuration file
        print("Reading program paths from existing configuration file ...")
        external_paths = read_program_paths(path)

        # Rename existing file
        os.rename(path, f"{path}_OLD")

    with open(path, "w", newline=None) as rcfile:
        parser = configparser.ConfigParser()

        # collect all default settings from parts and feed them into the parser
        global parts
        from .part import CensoPart

        parts["general"] = CensoPart
        parser.read_dict(
            {
                partname: {
                    settingname: setting["default"]
                    for settingname, setting in part.get_options().items()
                }
                for partname, part in parts.items()
            }
        )

        # Try to get paths from 'which'
        if external_paths is None:
            print("Trying to determine program paths automatically ...")
            external_paths = find_program_paths()

        parser["paths"] = external_paths

        print(f"Writing new configuration file to {path} ...")
        parser.write(rcfile)

    print(
        f"\nA new configuration file was written into {path}.\n"
        "You should adjust the settings to your needs and set the program paths.\n"
        "Right now the settings are at their default values.\n"
    )

    if CENSORCNAME not in path:
        print(
            f"Additionally make sure that the file name is '{CENSORCNAME}'.\n"
            f"Currently it is '{os.path.split(path)[-1]}'.\n"
        )


def read_program_paths(path: str) -> dict[str, str] | None:
    """
    Read program paths from the configuration file at 'path'
    """
    with open(path, "r") as inp:
        parser = configparser.ConfigParser()
        parser.read_file(inp)

    try:
        return dict(parser["paths"])
    except KeyError:
        print(f"WARNING: No paths found in {path}")
        return None


def find_program_paths() -> dict[str, str]:
    """
    Try to determine program paths automatically
    """
    # TODO - for now only the most important ones are implemented
    mapping = {
        "orcapath": "orca",
        "xtbpath": "xtb",
        "crestpath": "crest",
        "cosmorssetup": None,
        "dbpath": None,
        "cosmothermversion": None,
        "mpshiftpath": None,
        "escfpath": None,
    }
    paths = {}

    for pathname, program in mapping.items():
        if program is not None:
            path = shutil.which(program)
        else:
            path = None

        if path is not None:
            paths[pathname] = path
        else:
            paths[pathname] = ""

    # if orca was found try to determine orca version from the path (kinda hacky)
    if paths["orcapath"] != "":
        try:
            paths["orcaversion"] = (
                paths["orcapath"].split(os.sep)[-2][5:10].replace("_", ".")
            )
        except Exception:
            paths["orcaversion"] = ""

    return paths


def find_rcfile() -> str | None:
    """
    check for existing .censorc2 in $home dir
    """

    rcpath = None
    # check for .censorc in $home
    if os.path.isfile(os.path.join(os.path.expanduser("~"), CENSORCNAME)):
        rcpath = os.path.join(os.path.expanduser("~"), CENSORCNAME)

    return rcpath


def override_rc(args: Namespace) -> None:
    """
    Override the settings from the rcfile (or default settings) with settings from the command line.

    Args:
        args(Namespace): Namespace generated by command line parser.

    Returns:
        None
    """
    # Override general and part specific settings
    # TODO - might be made nicer by using the argument groups?
    global parts
    from .part import CensoPart

    for part in list(parts.values()) + [CensoPart]:
        part_settings = part.get_settings()
        for setting in part_settings.keys():
            if getattr(args, setting, None) is not None:
                part.set_setting(setting, getattr(args, setting))


# __settings_options = {
# "optrot": {
#     "func": {
#         "default": "pbe-d4",
#         "options": dfa_settings.find_func("optrot")
#     },
#     "func_or_scf": {
#         "default": "r2scan-3c",
#         "options": []
#     },
#     "basis": {
#         "default": "def2-SVPD",
#         "options": basis_sets
#     },
#     "prog": {
#         "default": "orca",
#         "options": [
#             "orca"
#         ]
#     },
#     "run": {
#         "default": False
#     },
#     "freq_or": {
#         "default": [
#             598.0
#         ]
#     }
# },
# "uvvis": {
#     "nroots": {
#         "default": 20,
#         "range": [
#             1,
#             100
#         ]
#     },
#     "sigma": {
#         "default": 0.1,
#         "range": [
#             0.1,
#             1.0
#         ]
#     },
#     "run": {
#         "default": False
#     }
# },
# }
