# Licensed under a 3-clause BSD style license - see LICENSE.rst
# -*- coding: utf-8 -*-
"""Test desispec.scripts.
"""
from __future__ import absolute_import, division
# The line above will help with 2to3 support.

import os
import unittest


class TestScripts(unittest.TestCase):
    """Test desispec.scripts.
    """

    @classmethod
    def setUpClass(cls):
        # from os import environ
        # for k in ('DESI_SPECTRO_REDUX', 'SPECPROD'):
        #     if k in environ:
        #         raise AssertionError("{0}={1} was pre-defined in the environment!".format(k, environ[k]))
        cls.environ_cache = dict()

    @classmethod
    def tearDownClass(cls):
        cls.environ_cache.clear()

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def dummy_environment(self, env):
        """Set dummy environment variables for testing.

        Parameters
        ----------
        env : :class:`dict`
            Mapping of variables to values
        """
        from os import environ
        for k in env:
            if k in environ:
                self.environ_cache[k] = environ[k]
            else:
                self.environ_cache[k] = None
            environ[k] = env[k]
        return

    def clear_environment(self):
        """Reset environment variables after a test.
        """
        from os import environ
        for k in self.environ_cache:
            if self.environ_cache[k] is None:
                del environ[k]
            else:
                environ[k] = self.environ_cache[k]
        self.environ_cache.clear()
        return

    def test_delivery(self):
        """Test desispec.scripts.delivery.
        """
        from ..scripts.delivery import parse_delivery
        with self.assertRaises(SystemExit):
            options = parse_delivery([])
        with self.assertRaises(SystemExit):
            options = parse_delivery('filename', '2', '20170317', 'foo')
        with self.assertRaises(SystemExit):
            options = parse_delivery('filename', 'foo', '20170317', 'start')
        options = parse_delivery('filename', '2', '20170317', 'start')
        self.assertEqual(options.filename, 'filename')
        self.assertEqual(options.exposure, 2)
        self.assertEqual(options.night, '20170317')
        self.assertEqual(options.nightStatus, 'start')


def test_suite():
    """Allows testing of only this module with the command::

        python setup.py test -m <modulename>
    """
    return unittest.defaultTestLoader.loadTestsFromName(__name__)
