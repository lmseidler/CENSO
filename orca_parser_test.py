import unittest
import os
from functools import reduce
from collections import OrderedDict

from censo.orca_processor import OrcaParser

test_dir = os.getcwd()

class OParserTest(unittest.TestCase):
    def setUp(self):
        self.test = OrcaParser()

    
    def test_read(self):
        inp = self.test.read_input(os.path.join(test_dir, "testfiles", "inp"))

        should = OrderedDict()
        should["main"] = ["RHF", "CCSD(T)", "def2-TZVP", "TightSCF"]
        should["paras"] = {"R=": ["4.0,0.5,35"]}
        should["geom"] = {}
        should["geom"]["def"] = ["xyz", "0", "1"]
        should["geom"]["coord"] = [
            ["H", "0", "0", "0"],
            ["F", "0", "0", "{R}"],
        ]

        self.assertDictEqual(inp, should)


    def test_write(self):
        towrite = OrderedDict()
        towrite["main"] = ["RHF", "CCSD(T)", "def2-TZVP", "TightSCF"]
        towrite["paras"] = {"R=": ["4.0,0.5,35"]}
        towrite["geom"] = {}
        towrite["geom"]["def"] = ["xyz", "0", "1"]
        towrite["geom"]["coord"] = [
            ["H", "0", "0", "0"],
            ["F", "0", "0", "{R}"],
        ]

        self.test.write_input(os.path.join(test_dir, "testfiles", "testinp"), towrite)
        with open(os.path.join(test_dir, "testfiles", "testinp"), "r") as file:
            written = file.readlines()

        with open(os.path.join(test_dir, "testfiles", "inp2"), "r") as file:
            should = file.readlines()

        self.assertListEqual(written, should)