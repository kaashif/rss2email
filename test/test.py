#!/usr/bin/env python3

"""Test processing logic on known feeds.
"""

import difflib as _difflib
import glob as _glob
import io as _io
import logging as _logging
import os as _os
import re as _re
import tempfile
import multiprocessing
import subprocess
import unittest

import rss2email as _rss2email
import rss2email.config as _rss2email_config
import rss2email.feed as _rss2email_feed

class TestEmails(unittest.TestCase):
    class Send (list):
        def __call__(self, sender, message):
            self.append((sender, message))

        def as_string(self):
            chunks = [
                'SENT BY: {}\n{}\n'.format(sender, message.as_string())
                for sender,message in self]
            return '\n'.join(chunks)

    def __init__(self, *args, **kwargs):
        super(TestEmails, self).__init__(*args, **kwargs)

        # Get a copy of the internal rss2email.CONFIG for copying
        _stringio = _io.StringIO()
        _rss2email_config.CONFIG.write(_stringio)
        self.BASE_CONFIG_STRING = _stringio.getvalue()
        del _stringio

        self.MESSAGE_ID_REGEXP = _re.compile(
            '^Message-ID: <[^@]*@dev.null.invalid>$', _re.MULTILINE)
        self.USER_AGENT_REGEXP = _re.compile(
            '^User-Agent: rss2email/[0-9.]* (\S*)$', _re.MULTILINE)
        self.BOUNDARY_REGEXP = _re.compile('===============[^=]+==')

    def clean_result(self, text):
        """Cleanup dynamic portions of the generated email headers

        >>> text = (
        ...      'Content-Type: multipart/digest;\\n'
        ...      '  boundary="===============7509425281347501533=="\\n'
        ...      'MIME-Version: 1.0\\n'
        ...      'Date: Tue, 23 Aug 2011 15:57:37 -0000\\n'
        ...      'Message-ID: <9dff03db-f5a7@dev.null.invalid>\\n'
        ...      'User-Agent: rss2email/3.5 (https://github.com/rss2email/rss2email)\\n'
        ...      )
        >>> print(clean_result(text).rstrip())
        Content-Type: multipart/digest;
        boundary="===============...=="
        MIME-Version: 1.0
        Date: Tue, 23 Aug 2011 15:57:37 -0000
        Message-ID: <...@dev.null.invalid>
        User-Agent: rss2email/...
        """
        for regexp,replacement in [
                (self.MESSAGE_ID_REGEXP, 'Message-ID: <...@dev.null.invalid>'),
                (self.USER_AGENT_REGEXP, 'User-Agent: rss2email/...'),
                (self.BOUNDARY_REGEXP, '===============...=='),
        ]:
            text = regexp.sub(replacement, text)
        return text

    def run_single_test(self, dirname=None, config_path=None, force=False):
        if dirname is None:
            dirname = _os.path.dirname(config_path)
        if config_path is None:
            _rss2email.LOG.info('testing {}'.format(dirname))
            for config_path in _glob.glob(_os.path.join(dirname, '*.config')):
                self.run_single_test(dirname=dirname, config_path=config_path, force=force)
            return
        feed_path = _glob.glob(_os.path.join(dirname, 'feed.*'))[0]

        _rss2email.LOG.info('testing {}'.format(config_path))
        config = _rss2email_config.Config()
        config.read_string(self.BASE_CONFIG_STRING)
        read_paths = config.read([config_path])
        feed = _rss2email_feed.Feed(name='test', url=feed_path, config=config)
        expected_path = config_path.replace('config', 'expected')
        with open(expected_path, 'r') as f:
            expected = self.clean_result(f.read())
        feed._send = TestEmails.Send()
        feed.run()
        generated = feed._send.as_string()
        if force:
            with open(expected_path, 'w') as f:
                f.write(generated)
        generated = self.clean_result(generated)
        if generated != expected:
            diff_lines = _difflib.unified_diff(
                expected.splitlines(), generated.splitlines(),
                'expected', 'generated', lineterm='')
            raise ValueError(
                'error processing {}\n{}'.format(
                    config_path,
                    '\n'.join(diff_lines)))

    def test_send(self):
        "Emails generated from already-fetched feed data are correct"
        # no paths on the command line, find all subdirectories
        this_dir = _os.path.dirname(__file__)
        test_dirs = []
        for basename in _os.listdir(this_dir):
            path = _os.path.join(this_dir, basename)
            if _os.path.isdir(path):
                test_dirs.append(path)

        # we need standardized URLs, so change to `this_dir` and adjust paths
        orig_dir = _os.getcwd()
        _os.chdir(this_dir)

        # run tests
        for orig_path in test_dirs:
            this_path = _os.path.relpath(orig_path, start=this_dir)
            if _os.path.isdir(this_path):
                self.run_single_test(dirname=this_path)
            else:
                self.run_single_test(config_path=this_path)

class TestFetch(unittest.TestCase):
    "Retrieving feeds from servers"
    def test_delay(self):
        "Waits before fetching repeatedly from the same server"
        wait_time = 0.3
        delay_cfg = """[DEFAULT]
        to = example@example.com
        same-server-fetch-interval = {}
        """.format(wait_time)
        tmpdir = tempfile.mkdtemp()
        cfg_path = _os.path.join(tmpdir, "rss2email.cfg")
        data_path = _os.path.join(tmpdir, "rss2email.json")

        def r2e(*args):
            subprocess.call(["r2e", "-c", cfg_path, "-d", data_path] + list(args))

        with open(cfg_path, "w") as f:
            f.write(delay_cfg)

        num_requests = 3

        def webserver(queue):
            import http.server, time

            class NoLogHandler(http.server.SimpleHTTPRequestHandler):
                def log_message(self, format, *args):
                    return

            with http.server.HTTPServer(('', 0), NoLogHandler) as httpd:
                port = httpd.server_address[1]
                queue.put(port)

                start = 0
                for _ in range(num_requests):
                    httpd.handle_request()
                    end = time.time()
                    if end - start < wait_time:
                        queue.put("too fast")
                        return
                    start = end
                queue.put("ok")

        queue = multiprocessing.Queue()
        webserver_proc = multiprocessing.Process(target=webserver, args=(queue,))
        webserver_proc.start()
        port = queue.get()

        for i in range(num_requests):
            r2e("add", f'test{i}', f'http://127.0.0.1:{port}/disqus/feed.rss')

        r2e("run", "--no-send")
        result = queue.get()

        _os.remove(cfg_path)
        _os.remove(data_path)
        _os.rmdir(tmpdir)

        if result == "too fast":
            raise Exception("r2e did not delay long enough!")

if __name__ == '__main__':
    unittest.main()
