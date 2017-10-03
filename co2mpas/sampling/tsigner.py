#!/usr/bin/env python
#
# Copyright 2014-2017 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl
"""A *report* contains the co2mpas-run values to time-stamp and disseminate to TA authorities & oversight bodies."""

from collections import (
    defaultdict, OrderedDict, namedtuple, Mapping)  # @UnusedImport
import io
import os
import re
import stat
import sys
from typing import (
    List, Sequence, Iterable, Text, Tuple, Dict, Callable, Union)  # @UnusedImport

from pandalone import utils as pndlu

from co2mpas.sampling.dirlock import locked_on_dir
import os.path as osp

from . import CmdException, baseapp, crypto, tstamp
from .._vendor import traitlets as trt


HEAD_FNAME = 'HEAD'
LOCK_FNAME = '$$tsigner-lockfile$$'
ROOT_PARENT_ID = '<root>'


def pgp_sig_to_hex(sig_id: Text) -> int:
    import base64
    import binascii

    sig_bytes = base64.b64decode(sig_id + '==')
    return binascii.hexlify(sig_bytes).decode()


class TsignerSpec(baseapp.Spec):
    stamper_name = trt.Unicode(
        'JRC-stamper',
        help="By default, that `stamp_chain_dir` is derived from this name. ",
        config=True)

    __stamp_auth = None

    @property
    def _stamp_auth(self):
        if not self.__stamp_auth:
            self.__stamp_auth = crypto.get_stamper_auth(config=self.config)
        return self.__stamp_auth


class SigChain(TsignerSpec):
    """
    Manage the list of signatures stored in git-like 2-letter folders.
    """

    stamp_chain_dir = trt.Unicode(
        help="""The folder to store all signed stamps and derive cert-chain.""",
        config=True
    )

    @trt.default('stamp_chain_dir')
    def _default_chain_dir(self):
        service_fname = re.sub(r'\W', '_', self.stamper_name)
        return osp.join(baseapp.default_config_dir(), service_fname)

    read_only_files = trt.Bool(
        True,
        help="""
        If true, write chain-stamps as READ_ONLY (permission and attribute).
        """,
        config=True
    )

    parent_sig_regex = trt.CRegExp(
        r"""# parent_stamp: (\S+)""",
        config=True)

    @property
    def _head_fpath(self):
        return osp.join(self.stamp_chain_dir, HEAD_FNAME)

    @property
    def _lock_fpath(self):
        return osp.join(self.stamp_chain_dir, LOCK_FNAME)

    def _write_chain_head(self, count, parent_id):
        with open(self._head_fpath, 'wt') as fd:
            fd.write('0.0.0 %s %s' % (count, parent_id))

    def _read_chain_head(self):
        """Called only on non-empty chain-folders. """
        head_fpath = self._head_fpath
        with open(head_fpath, 'rt') as fd:
            first_line = fd.readline()
        version, count, parent_id = first_line.split()
        assert version == '0.0.0', (version, count, parent_id, head_fpath)
        count = int(count)

        return count, parent_id

    def _write_sig_file(self, text, sig_hex):
        dpath = osp.join(self.stamp_chain_dir, sig_hex[:2])
        fpath = osp.join(dpath, sig_hex[2:])
        os.makedirs(dpath, exist_ok=True)
        ## Write as bytes to avoid duplicating PGP ``r\n`` EOL
        #  into ``\r\r\n``.
        #
        with open(fpath, 'wb') as fd:
            fd.write(text.encode('utf-8'))
        ## Make file READ_ONLY.
        #
        if self.read_only_files:
            import shutil

            os.chmod(fpath, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
            if os.name == 'nt':
                import win32api
                import win32con

                ## From https://stackoverflow.com/a/14957883/548792
                win32api.SetFileAttributes(
                    fpath, win32con.FILE_ATTRIBUTE_READONLY)

            elif shutil.which('chattr') is not None:
                import subprocess as sbp

                sbp.check_call(['chattr', '+i', fpath])

    def _parse_sig_file(self, sig_hex):
        stamp_auth = self._stamp_auth
        sig_fpath = osp.join(self.stamp_chain_dir, sig_hex[:2], sig_hex[2:])

        ## Read as bytes to preserve PGP ``r\n`` EOLs
        #
        with open(sig_fpath, 'rb') as fd:
            sig = fd.read().decode('utf-8')
        sig = sig.replace('\r\r\n', '\r\n')
        verdict = stamp_auth.verify_clearsigned(sig)
        assert verdict, ("Invalid sig!", vars(verdict))
        verified_sig_hex = pgp_sig_to_hex(verdict.signature_id)
        assert sig_hex == verified_sig_hex, (
            "Stamp-chain file mismatch sig_id!", sig_fpath, verified_sig_hex)

        msg = crypto.pgp_split_clearsigned(sig)['msg']
        m = self.parent_sig_regex.search(msg)
        parent_id = m.group(1)

        return parent_id

    def _read_stamp_chain_dict(self, chain_dir):
        log = self.log

        ## { <parent-sign_id> --> <sign_id> }
        chain_dict = {}  # type: Mapping[Text, Text]
        for fname in os.listdir(chain_dir):
            if fname in (HEAD_FNAME, LOCK_FNAME):
                continue

            sig_dir = osp.join(chain_dir, fname)

            if osp.isdir(sig_dir) and len(fname) == 2:
                for sig_fname in os.listdir(sig_dir):
                    sig_fpath = osp.join(sig_dir, sig_fname)
                    sig_hex = fname + sig_fname
                    try:
                        parent_id = self._parse_sig_file(sig_hex)
                    except Exception as ex:
                        log.warning("Skipping stamp-chain file(%s) due to: %s",
                                    sig_fpath, ex, exc_info=1)
                    else:
                        assert parent_id not in chain_dict, (
                            "Forked stamp-chain history?!?",
                            parent_id, sig_hex, chain_dict[parent_id], chain_dict)
                        chain_dict[parent_id] = sig_hex

            else:
                log.warning("Skipping stamp-chain file: %s", sig_dir)

        if not chain_dict:
            log.warning("No stamps found in chain folder: %s", chain_dir)
        else:
            assert ROOT_PARENT_ID in chain_dict, (
                "No root found in chain folder!", chain_dir, chain_dict)

        return chain_dict

    def _load_or_rebuild_stamp_chain(self, revalidate=False):
        """
        Load old stamps from stamp-folder and chain them.

        :return:
            tuple(<count>, <last-sig_id>, <stamps-chain-list)
            last element exists only if `revalidate`.

        Stamps-chain folder structure::

            /
            +--HEAD: 0.0.0 <count> <last-sig_id>
            +--aa/
                +--ltrow213gq: <signed-text-for-`aaltrow213gq`-sig_id>
                +--..

        """
        log = self.log
        chain_dir = self.stamp_chain_dir
        assert osp.isdir(chain_dir), (
            "Missing or invalid stamp-chain dir!", chain_dir)
        stamps_folder_fnames = os.listdir(chain_dir)

        ## A *totally* empty stamp-chain dir (except lock-dir)
        #  means we are starting anew.
        #
        if len(stamps_folder_fnames) == 1:
            log.info("Starting in a new empty stamps-chain folder(%s).",
                     chain_dir)
            count, parent_id = 0, ROOT_PARENT_ID
            self._write_chain_head(count, parent_id)
            results = count, parent_id
            if revalidate:
                results += ([], )
            return results
        try:
            count, parent_id = self._read_chain_head()
        except Exception as ex:
            log.warning("Rebuilding stamp-chain HEAD(%s) due to: %s",
                        self._head_fpath, ex, exc_info=1)
            parent_id = count = None

        if parent_id and not revalidate:
            return count, parent_id

        # { <sign_id> --> <parent_sign_id> }
        # type: Mapping[Text, Text]
        chain_dict = self._read_stamp_chain_dict(chain_dir)
        chain = []
        if chain_dict:
            sig = ROOT_PARENT_ID
            while sig in chain_dict:
                sig = chain_dict.pop(sig)
                chain.append(sig)
            count = len(chain)
            parent_id = chain[-1]
            if chain_dict:
                log.warning("Found %s stray stamps from a %s-long stamp-chain: %s",
                            len(chain_dict), len(chain), chain_dict)
        else:
            count, parent_id = 0, ROOT_PARENT_ID

        self._write_chain_head(count, parent_id)

        results = count, parent_id
        if revalidate:
            results += (chain, )

        return results

    def _load_stamp_chain_head(self):
        return self._load_or_rebuild_stamp_chain()

    def load_stamp_chain(self):
        """
        :return
            tuple(count, parent_id, chain_list)
        """
        with locked_on_dir(self._lock_fpath):
            return self._load_or_rebuild_stamp_chain(revalidate=True)[-1]


class TsignerService(SigChain, tstamp.TstampReceiver):
    """
    To run securely on a server see: https://wiki.gnupg.org/AgentForwarding
    """

    sender = trt.Unicode(
        allow_none=True)

    recipients = trt.List(
        trt.Unicode(),
        allow_none=True)

    validate_decision = trt.Bool(
        True,
        help="""Validate dice and append-report at the bottom of dreport.  """,
        config=True)

    trim_dreport = trt.Bool(
        help="""Remove any garbage after dreport's signature? """,
        config=True)

    def sign_text_as_tstamper(self, text: Text,
                              sender: Text=None,
                              full_output: bool=False):
        """
        :param full_output:
            if true, return `gnupg` output object, otherwise, signed text.
        """
        from datetime import datetime

        stamper_name = self.stamper_name
        stamper_auth = self._stamp_auth
        recipients = '\n#     '.join(self.recipients)
        issue_date = datetime.now().isoformat()

        with locked_on_dir(self._lock_fpath):
            osp.join(self.stamp_chain_dir, LOCK_FNAME)
            stamp_count, parent_stamp = self._load_stamp_chain_head()
            stamp_count += 1
            tstamp_text = f"""\
########################################################
#
# Proof of posting certificate from {stamper_name}
# certifying that:-
#   {sender}
# requested to email this message to:-
#   {recipients}
#
# certificate_date: {issue_date}
# reference: {stamp_count:07}
# parent_stamp: {parent_stamp}
#
########################################################


{text}
"""
            stamper_comment = f"Stamper Reference Id: {stamp_count:07}"
            sign = stamper_auth.clearsign_text(
                tstamp_text, extra_args=['--comment', stamper_comment],
                full_output=full_output)

            stamp = str(sign)
            ver = stamper_auth.verify_clearsigned(stamp)
            sig_hex = pgp_sig_to_hex(ver.signature_id)
            self._write_sig_file(stamp, sig_hex)
            self._write_chain_head(stamp_count, sig_hex)

            if full_output:
                #
                ## GnuPG does not return signature-id when signing :-(
                from toolz import dicttoolz as dtz

                ## Merge gnupg-verify results
                #
                ts_ver = dtz.valfilter(bool, vars(ver))
                sign.__dict__.update(ts_ver)

            return sign

    def sign_dreport_as_tstamper(self, dreport: Text):
        import pprint as pp
        import textwrap as tw

        tag_verdict = self.parse_signed_tag(dreport)
        # TODO: move sig-validation check in `crypto` module.
        tag_signer = crypto.uid_from_verdict(tag_verdict)
        if tag_verdict['valid']:
            if self.trim_dreport:
                dreport = tag_verdict['parts']['msg'].decode('utf-8')
        else:
            err = "Invalid dice-report due to: %s \n%s" % (
                tag_verdict['status'], tw.indent(pp.pformat(tag_verdict), '  '))
            raise CmdException(err)

        ## Check if test-key still used.
        #
        git_auth = crypto.get_git_auth(config=self.config)
        git_auth.check_test_key_missused(tag_verdict['key_id'])

        sender = self.sender or tag_signer or '<unknown>'

        sign = self.sign_text_as_tstamper(
            dreport, sender, full_output=True)

        stamp, ts_verdict = str(sign), vars(sign)
        tag_name = self.extract_dice_tag_name(None, dreport)
        dice_decision = self.make_dice_results(ts_verdict,
                                               tag_verdict,
                                               tag_name)
        if self.validate_decision:
            signed_text = self.append_decision(stamp, dice_decision)
        else:
            signed_text = stamp

        return signed_text, dice_decision


class TsignerCmd(baseapp.Cmd):
    """Private stamper service."""

    list = trt.Bool(
        help="Print all stamp-chain stored (no other args accepted).",
        config=True
    )

    def __init__(self, **kwds):
        kwds.setdefault('conf_classes', [TsignerService,
                                         crypto.GitAuthSpec,
                                         crypto.StamperAuthSpec])
        kwds.setdefault('cmd_flags', {
            ('l', 'list'): (
                {
                    'TsignerCmd': {'list': True},
                },
                type(self).list.help
            ),
        })
        super().__init__(**kwds)

    def _list_stamps(self, *sig_hex_prefixes):
        chainer = SigChain(config=self.config)
        if not sig_hex_prefixes:
            chain_list = chainer.load_stamp_chain()
            yield "stamps_count: %s" % len(chain_list)
            yield from chain_list
        else:
            raise Exception("Not impl!")

    def _sign_stamps(self, *files):
        from boltons.setutils import IndexedSet as iset

        signer = TsignerService(config=self.config)

        files = iset(files) or ['-']
        self.log.info("Signining '%s'...", tuple(files))

        for file in files:
            if file == '-':
                self.log.info("Reading STDIN; paste message verbatim!")
                mail_text = sys.stdin.read()
            else:
                self.log.debug("Reading '%s'...", pndlu.convpath(file))
                with io.open(file, 'rt') as fin:
                    mail_text = fin.read()

            try:
                sig_text, _decision = signer.sign_dreport_as_tstamper(mail_text)

                ## In PY3 stdout duplicates \n as \r\n, hence \r\n --> \r\r\n.
                #  and signed text always has \r\n EOL.
                yield sig_text.replace('\r\n', '\n')
            except Exception as ex:
                self.log.error("%s: signig %i-char message failed due to: %s",
                               file, len(mail_text), ex, exc_info=1)

    def run(self, *args):
        if self.list:
            yield from self._list_stamps(*args)
        else:
            yield from self._sign_stamps(*args)
