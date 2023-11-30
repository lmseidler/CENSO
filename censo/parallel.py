"""
Performs the parallel execution of the QM calls.
"""
from functools import reduce
import os
from multiprocessing import Semaphore
from typing import Any, Dict, List, Tuple
from concurrent.futures import ProcessPoolExecutor, as_completed
import atexit

from censo.procfact import ProcessorFactory
from censo.qm_processor import QmProc
from censo.utilities import print
from censo.datastructure import GeometryData, MoleculeData
from censo.params import OMPMIN, OMPMAX

# get number of cores
global ncores
ncores = os.cpu_count()


class ParallelJob:
    def __init__(self, conf: GeometryData, jobtype: List[str], omp: int):
        # conformer for the job
        self.conf = conf

        # list of jobtypes to execute for the processor
        self.jobtype = jobtype

        # number of cores to use
        self.omp = omp

        # stores path to an mo file which is supposed to be used as a guess
        self.mo_guess = None

        # store metadata, is updated by the processor
        # structure e.g.: {"sp": {"success": True, "error": None}, "xtb_rrho": {"success": False, ...}, ...}
        # always contains the "mo_path" key
        self.meta: Dict[str, Any] = {j: {} for j in jobtype}
        self.meta["mo_path"] = None

        # stores all flags for the jobtypes
        self.flags: Dict[str, Any] = {}


def execute(conformers: List[MoleculeData], instructions: Dict[str, Any], workdir: str) -> Dict[int, Any]:
    global ncores

    # try to get program from instructions
    prog = instructions.get("prog", None)

    if prog is None:
        raise RuntimeError("Could not determine program from instructions.")

    # initialize the processor for the respective program
    processor = ProcessorFactory.create_processor(
        prog,
        instructions,
        workdir,
    )

    balance = instructions["balance"]

    # create jobs
    jobs = [ParallelJob(conf.geom, instructions["jobtype"], instructions["omp"]) for conf in conformers]

    if instructions["copy_mo"]:
        # check for the most recent mo files for each conformer
        # TODO - how would this work when multiple different programs are supported?
        for job in jobs:
            try:
                job.mo_guess = next(c for c in conformers if c.geom.id == job.conf.id).mo_paths[-1]
            except IndexError:
                pass

    # set cores per process for each job 
    if balance:
        set_omp_chunking(jobs)
    else:
        set_omp_constant(jobs, instructions["omp"])

    # execute the jobs
    results = dqp(jobs, processor)

    # assert that there is a result for every conformer
    try:
        assert all(job.conf.id in results.keys() for job in jobs)
    except AssertionError:
        raise RuntimeError(
            "There is a mismatch between conformer ids and returned results. Cannot find at least one conformer id in results.")

    # if 'copy_mo' is enabled, try to get the mo_path from metadata and store it in the respective conformer object
    if instructions["copy_mo"]:
        mo_paths = {job.conf.id: job.meta["mo_path"] for job in jobs}
        for conf in conformers:
            if mo_paths[conf.geom.id] is not None:
                conf.mo_paths.append(mo_paths[conf.geom.id]["mo_path"])

    # create a new list of failed jobs that should be restarted with special flags
    if instructions["retry_failed"]:
        # determine failed jobs
        failed_jobs = [i for i, job in enumerate(jobs) if any(not job.meta[jt]["success"] for jt in job.jobtype)]

        if len(failed_jobs) != 0:

            # contains jobs that should be retried (depends if the error can be handled or not)
            retry = []

            # determine flags for jobs based on error messages
            for failed_job in failed_jobs:
                for jt in jobs[failed_job].jobtype:
                    # for now only sp and gsolv calculations are caught
                    if not jobs[failed_job].meta[jt]["success"] and jt in ["sp", "gsolv"]:
                        if jobs[failed_job].meta[jt]["error"] == "SCF not converged":
                            retry.append(failed_job)
                            jobs[failed_job].flags[jt] = "scf_not_converged"
                    # remove all successful jobs from jobtype to avoid re-execution
                    elif jobs[failed_job].meta[jt]["success"]:
                        jobs[failed_job].jobtype.remove(jt)

            # execute jobs that should be retried
            print(f"Restarting {len(retry)} jobs.")
            set_omp_chunking([jobs[i] for i in retry])
            results.update(dqp([jobs[i] for i in retry], processor))

            # again, try to get the mo_path from metadata and store it in the respective conformer object
            if instructions["copy_mo"]:
                mo_paths = {job.conf.id: job.meta["mo_path"] for job in [jobs[i] for i in retry]}
                for conf in conformers:
                    if mo_paths[conf.geom.id] is not None:
                        conf.mo_paths.append(mo_paths[conf.geom.id]["mo_path"])
        else:
            print("All jobs executed successfully.")

    return results


def dqp(jobs: List[ParallelJob], processor: QmProc) -> dict[int, Any]:
    """
    D ynamic Q ueue P rocessing
    """

    global ncores

    # execute calculations for given list of conformers
    executor = ProcessPoolExecutor(max_workers=ncores // min(job.omp for job in jobs))

    # make sure that the executor exits gracefully on termination
    # TODO - is using wait=False a good option here?
    # should be fine since workers will kill programs with SIGTERM
    # wait=True leads to the workers waiting for their current task to be finished before terminating
    atexit.register(executor.shutdown, wait=False)

    # semaphore to keep track of the number of free cores
    free_cores: Semaphore = Semaphore(ncores)

    # define a callback function that is called everytime a job is finished
    # it's purpose is to release the resources from the semaphore
    def callback(f):
        nonlocal free_cores
        args = f.args

        # args[0] is the job
        free_cores.release(args[0].omp)

    # sort the jobs by the number of cores used
    # (the first item will be the one with the lowest number of cores)
    jobs.sort(key=lambda x: x.omp)

    tasks = []
    for job in jobs:
        # wait until enough cores are free
        free_cores.acquire(job.omp)

        # submit the job
        tasks.append(executor.submit(processor.run, job))
        tasks[-1].add_done_callback(callback)

    # wait for all jobs to finish and collect results
    results = [task.result() for task in as_completed(tasks)]

    # merge the results
    # structure of results
    #   e.g. {id(conf): {"xtb_gsolv": {"gsolv": ..., "energy_xtb_gas": ...}, ...}, ...}
    return reduce(lambda x, y: {**x, **y}, results)


def set_omp_constant(jobs: List[ParallelJob], omp: int) -> None:
    """
    Sets the number of cores that are supposed to be used for every job.
    This just takes the user defined settings (or default settings) and applies them for every job.
    """
    # print a warning if omp is less than OMPMIN
    if omp < OMPMIN:
        print(f"WARNING: omp ({omp}) is less than {OMPMIN}, the recommended value for efficient parallelization.")

    for job in jobs:
        job.omp = omp


def set_omp_chunking(jobs: list[ParallelJob]) -> None:
    """
    Determines and sets the number of cores that are supposed to be used for every job.
    This method is efficient if it can be assumed that the jobs take roughly the same amount of time each.
    Each job shouldn't use less than OMPMIN cores.
    """
    global ncores  # Access the global variable ncores

    # Get the total number of jobs
    jobs_left, tot_jobs = len(jobs), len(jobs)

    # Calculate the maximum and minimum number of processes (number of jobs that can be executed simultaneously)
    maxprocs = ncores // OMPMIN  # Calculate the maximum number of processes
    minprocs = max(1, ncores // OMPMAX)  # Calculate the minimum number of processes

    # Loop until all jobs are distributed
    while jobs_left > 0:
        if jobs_left >= maxprocs:
            p = maxprocs  # Set the number of processes to the maximum if there are enough jobs left
        elif jobs_left == maxprocs:
            p = jobs_left  # Set the number of processes to the remaining jobs if there are exactly maxprocs jobs left
        elif jobs_left < minprocs:
            p = minprocs  # Set the number of processes to the minimum if there are less jobs left than minprocs
        else:
            # Find the largest number of processes that evenly divides the remaining jobs
            p = max([j for j in range(minprocs, maxprocs) if ncores % j == 0 and j <= jobs_left])

        # Set the number of cores for each job for as many jobs as possible before moving onto the next omp value
        while jobs_left - p >= 0:
            for job in jobs[tot_jobs - jobs_left:tot_jobs - jobs_left + p]:
                job.omp = ncores // p  # Set the number of cores for each job
            jobs_left -= p  # Decrement the number of remaining jobs
