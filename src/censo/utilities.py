"""
Utility functions which are used in the CENSO modules. From creating folders to
printout routines.
"""
import functools
import hashlib
import json
import os
import time
import re
from builtins import print as print_orig
from collections import OrderedDict
from collections.abc import Callable

from .params import CODING, BOHR2ANG
from .logging import setup_logger

logger = setup_logger(__name__)


class DfaHelper:
    _dfa_dict: dict

    @classmethod
    def set_dfa_dict(cls, dfadict_path: str):
        with open(dfadict_path, "r") as f:
            cls._dfa_dict = json.load(f)

    @classmethod
    def find_func(cls, part: str, prog=None):
        """
        Returns all functionals available for a given part and (optionally) qm program.

        Args:
            part (str): The name of the part.
            prog (str, optional): The qm program name. Defaults to None.

        Returns:
            list[str]: The list of functionals.
        """
        if prog is None:
            return [
                func for func, v in cls._dfa_dict["functionals"].items()
                if part in v["part"]
            ]
        else:
            return [
                func for func, v in cls._dfa_dict["functionals"].items()
                if part in v["part"] and v[prog] != ""
            ]

    @classmethod
    def get_name(cls, func: str, prog: str):
        """
        Returns the name of a certain functional in the given qm program. If name could not
        be found, the string passed as func will be returned instead.

        Args:
            func (str): The functional.
            prog (str): The qm program.

        Returns:
            str: The name of the functional.
        """
        if func in cls._dfa_dict["functionals"].keys():
            name = cls._dfa_dict["functionals"][func][prog]
        else:
            logger.warning(
                f"Functional {func} not found for program {prog}. Applying name literally.")
            name = func
        return name

    @classmethod
    def get_disp(cls, func: str):
        """
        Returns the dispersion correction of a given functional. If dispersion correction 
        cannot be determined, apply none.

        Args:
            func (str): The functional.

        Returns:
            str: The dispersion correction name.
        """
        if func in cls._dfa_dict["functionals"].keys():
            disp = cls._dfa_dict["functionals"][func]["disp"]
        else:
            logger.warning(
                f"Could not determine dispersion correction for {func}. Applying none.")
            disp = "novdw"
        return disp

    @classmethod
    def get_type(cls, func: str):
        """
        Returns the type of a certain functional. If the type cannot be determined, it 
        is assumed to be a GGA.

        Args:
            func (str): The functional.

        Returns:
            str: The type of the functional.
        """
        if func in cls._dfa_dict["functionals"].keys():
            rettype = cls._dfa_dict["functionals"][func]["type"]
        else:
            logger.warning(
                f"Could not determine functional type for {func}. Assuming GGA.")
            rettype = "GGA"
        return rettype

    @classmethod
    def functionals(cls) -> dict[str, dict]:
        return cls._dfa_dict["functionals"]


class SolventHelper:
    """
    Helper class to manage solvent lookup.
    """
    @classmethod
    def set_solvent_dict(cls, solvent_dict_path: str) -> None:
        """
        Load the solvents lookup dict.

        Args:
            solvent_dict_path (str): The path to the solvents lookup dict.
        """
        with open(solvent_dict_path, "r") as f:
            cls._solv_dict = json.load(f)

    @classmethod
    def get_solvent(cls, sm: str, name: str) -> str | None:
        """
        Try to lookup the solvent model keyword for the given solvent name. If it is not found, return None.

        Args:
            sm (str): The solvent model.
            name (str): The solvent name.

        Returns:
            str | None: The solvent model keyword or None if not found.
        """
        for keyword, names in cls._solv_dict[sm.lower()].items():
            if name.lower() in names:
                return keyword
        return None


def print(*args, **kwargs):
    """
    patch print to always flush
    """
    sep = " "
    end = "\n"
    file = None
    flush = True
    for key, value in kwargs.items():
        if key == "sep":
            sep = value
        elif key == "end":
            end = value
        elif key == "file":
            file = value
        elif key == "flush":
            flush = value
    print_orig(*args, sep=sep, end=end, file=file, flush=flush)


def format_data(
        headers: list[str], rows: list[list[any]], units: list[str] = None, sortby: int = 0, padding: int = 6
) -> list[str]:
    """
    Generates a formatted table based on the given headers, rows, units, and sortby index.
    """
    def natural_sort_key(s):
        """
        Natural sorting key for strings.
        """
        return [int(text) if text.isdigit() else text for text in re.split("(\d+)", s)]

    # Determine the maximum content length for each column after stripping
    # leading whitespace, to keep all rows equal length.
    max_content_lengths = [
        max(len(str(row[idx]).lstrip()) for row in rows) for idx in range(len(headers))
    ]

    # Adjust column widths based on maximum content length and padding
    collens = {
        header: max(len(header), max_content_length) + padding
        for header, max_content_length in zip(headers, max_content_lengths)
    }

    lines = []

    # Add table header
    header_line = " ".join(
        f"{header:^{collens[header]}}" for header in headers)
    lines.append(header_line)

    if units is not None:
        unit_line = " ".join(
            f"{unit:^{collens[headers[idx]]}}" for idx, unit in enumerate(units))
        lines.append(unit_line)

    # Sort rows based on the specified column
    if isinstance(rows[0][sortby], str) and rows[0][sortby].replace(".", "", 1).isdigit():
        rows.sort(key=lambda x: float(x[sortby]))
    else:
        rows.sort(key=lambda x: natural_sort_key(x[sortby]))

    # Add rows, stripping leading whitespace and aligning each cell
    for row in rows:
        row_line = " ".join(
            f"{str(value).lstrip():^{collens[headers[idx]]}}" for idx, value in enumerate(row)
        )
        lines.append(row_line)

    return lines


def frange(start: float, end: float, step: float = 1) -> list[float]:
    """
    Creates a range of floats, adding 'step' to 'start' while it's less or equal than 'end'.

    Args:
        start (float): The start of the range.
        end (float): The end of the range.
        step (float, optional): The step size. Defaults to 1.

    Returns:
        list[float]: The list of floats.
    """
    result = []
    current = start
    while current <= end:
        result.append(current)
        current += step
    return result


def t2x(
    path: str, writexyz: bool = False, outfile: str = "original.xyz"
) -> tuple[list, int, str]:
    """
    convert TURBOMOLE coord file to xyz data and/or write *.xyz output

     - path [abs. path] either to dir or file directly
     - writexyz [bool] default=False, directly write to outfile
     - outfile [filename] default = 'original.xyz' filename of xyz file which
                        is written into the same directory as
     returns:
     - coordxyz --> list of strings including atom x y z information
     - number of atoms
    """
    # read lines from coord file
    with open(path, "r", encoding=CODING, newline=None) as f:
        coord = f.readlines()

    # read coordinates with atom labels directly into a string
    # and append the string to a list to be written/returned later
    xyzatom = []
    for line in coord:
        if "$end" in line:  # stop at $end ...
            break
        xyzatom.append(
            functools.reduce(
                lambda x, y: x + " " + y,
                [
                    f"{float(line.split()[0]) * BOHR2ANG:.10f}",
                    f"{float(line.split()[1]) * BOHR2ANG:.10f}",
                    f"{float(line.split()[2]) * BOHR2ANG:.10f}",
                    f"{str(line.split()[3].lower()).capitalize()}",
                ],
            )
        )

    # get path from args without the filename of the ensemble (last element of path)
    if os.path.isfile(path):
        outpath = functools.reduce(
            lambda x, y: os.path.join(x, y), list(
                os.path.split(path))[::-1][1:][::-1]
        )
    # or just use the given path if it is not a file path
    else:
        outpath = path

    # write converted coordinates to xyz outfile if wanted
    if writexyz:
        with open(os.path.join(outpath, outfile), "w", encoding=CODING) as out:
            out.write(str(len(xyzatom)) + "\n")
            for line in xyzatom:
                out.write(line)
    return xyzatom, len(xyzatom), os.path.join(outpath, outfile)


def check_for_float(line: str) -> float | None:
    """Go through line and check for float, return first float"""
    elements = line.strip().split()
    value = None
    for element in elements:
        try:
            value = float(element)
        except ValueError:
            value = None
        if value:
            break
    return value


def do_md5(path):
    """
    Calculate md5 of file to identifly if restart happend on the same file!
    Input is buffered into smaller sizes to ease on memory consumption.
    Hashes entire content of ensemble input file to compare later
    """
    BUF_SIZE = 65536
    md5 = hashlib.md5()
    if os.path.isfile(path):
        with open(path, "rb") as f:
            while True:
                data = f.read(BUF_SIZE)
                if not data:
                    break
                md5.update(data)
        return md5.hexdigest()
    else:
        raise FileNotFoundError


def timeit(f) -> Callable:
    """
    time function execution
    timed function should have no return value, since it is lost in the process
    calling a decorated function returns the time spent for it's execution
    """

    @functools.wraps(f)
    def wrapper(*args, **kwargs) -> float:
        start = time.perf_counter()
        f(*args, **kwargs)
        end = time.perf_counter()
        return end - start

    return wrapper


def od_insert(
    od: OrderedDict[str, any], key: str, value: any, index: int
) -> OrderedDict[str, any]:
    """
    Insert a new key/value pair into an OrderedDict at a specific position.
    If it was a normal dict:
        od[key] = value, with insertion before the 'index'th key.

    Args:
        od: The OrderedDict to insert into.
        key: The key to insert.
        value: The value associated with the key.
        index: The index before which to insert the key/value pair.

    Returns:
        The updated OrderedDict.
    """
    # FIXME - somehow this doesn't work reliably, no idea why but sometimes the value is not inserted
    items: list[tuple[str, any]] = list(od.items())
    items.insert(index, (key, value))
    return OrderedDict(items)


def mad(trajectory1: list[float], trajectory2: list[float]) -> float:
    """
    Calculates the MAD (mean absolute deviation) between two trajectories.

    Args:
        trajectory1 (list[float]): The first trajectory.
        trajectory2 (list[float]): The second trajectory.

    Returns:
        float: The MAD.
    """
    try:
        assert len(trajectory1) == len(trajectory2)
    except AssertionError:
        raise ValueError("The trajectories must have the same length.")

    return sum(abs(x - y) for x, y in zip(trajectory1, trajectory2)) / len(trajectory1)


def mean_similarity(trajectories: list[list[float]]) -> float:
    """
    Calculates the mean similarity of a list of trajectories.

    Args:
        trajectories (list[list[float]]): The list of trajectories.

    Returns:
        float: The mean similarity.
    """
    # Calculate the MAD of each trajectory to every other trajectory
    similarities = []
    for i, trajectory1 in enumerate(trajectories):
        for _, trajectory2 in enumerate(trajectories[i + 1:]):
            similarities.append(mad(trajectory1, trajectory2))

    # Return the mean similarity
    # Unit: energy
    return sum(similarities) / len(similarities)
