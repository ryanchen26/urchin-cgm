import json
import os
import subprocess
import time
import urllib2
from datetime import datetime

import requests
from libpebble2.communication.transports.websocket import MessageTargetPhone
from libpebble2.communication.transports.websocket.protocol import AppConfigResponse
from libpebble2.communication.transports.websocket.protocol import AppConfigSetup
from libpebble2.communication.transports.websocket.protocol import WebSocketPhonesimAppConfig
from pebble_tool.sdk.emulator import ManagedEmulatorTransport

PLATFORMS = ('aplite', 'basalt')
PORT = os.environ['MOCK_SERVER_PORT']
MOCK_HOST = 'http://localhost:{}'.format(PORT)

CONSTANTS = json.loads(
    open(os.path.join(os.path.dirname(__file__), '../src/js/constants.json')).read()
)
BASE_CONFIG = CONSTANTS['DEFAULT_CONFIG']

def set_sgvs(sgvs):
    _post_mock_server('/set-sgv', sgvs)

def _post_mock_server(url, data):
    requests.post(MOCK_HOST + url, data=json.dumps(data))

def pebble_install_and_run(platforms):
    _call('pebble kill')
    _call('pebble clean')
    # TODO ensure this is called from the main project directory
    _call('pebble build')
    for platform in platforms:
        _call('pebble install --emulator {}'.format(platform))
    # Give the watchface time to show up
    time.sleep(10)

def set_config(config, platforms):
    for platform in platforms:
        emu = ManagedEmulatorTransport(platform)
        emu.connect()
        time.sleep(0.5)
        emu.send_packet(WebSocketPhonesimAppConfig(
            config=AppConfigSetup()),
            target=MessageTargetPhone()
        )
        time.sleep(0.5)
        emu.send_packet(WebSocketPhonesimAppConfig(
            config=AppConfigResponse(data=urllib2.quote(json.dumps(config)))),
            target=MessageTargetPhone()
        )
    # Wait for the watchface to re-render and request data
    time.sleep(0.5)

def pebble_screenshot(filename, platform):
    _call('pebble screenshot --emulator {} --no-open {}'.format(platform, filename))

def _call(command_str, **kwargs):
    print command_str
    return subprocess.Popen(
        command_str.split(' '),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **kwargs
    ).communicate()

def ensure_empty_dir(dirname):
    if os.path.isdir(dirname):
        _, err = _call('rm -r {}'.format(dirname))
        if err != '':
            raise Exception(err)
    os.mkdir(dirname)

def image_diff(test_file, gold_file, out_file):
    # requires ImageMagick
    _, diff = _call('compare -metric AE {} {} {}'.format(test_file, gold_file, out_file))
    return diff == '0'


class ScreenshotTest(object):
    @staticmethod
    def out_dir():
        return os.path.join(os.path.dirname(__file__), 'output')

    @classmethod
    def summary_filename(cls):
        return os.path.join(cls.out_dir(), 'screenshots.html')

    def circleci_url(self):
        if os.environ.get('CIRCLECI'):
            return 'https://circleci.com/api/v1/project/{}/{}/{}/artifacts/{}/$CIRCLE_ARTIFACTS/{}'.format(
                os.environ['CIRCLE_PROJECT_USERNAME'],
                os.environ['CIRCLE_PROJECT_REPONAME'],
                os.environ['CIRCLE_BUILD_NUM'],
                os.environ['CIRCLE_NODE_INDEX'],
                os.path.relpath(self.summary_filename(), os.path.dirname(__file__)),
            )
        else:
            return None

    @classmethod
    def test_filename(cls, platform):
        return os.path.join(cls.out_dir(), 'img', '{}-{}.png'.format(cls.__name__, platform))

    @classmethod
    def gold_filename(cls, platform):
        return os.path.join(os.path.dirname(__file__), 'gold', '{}-{}.png'.format(cls.__name__, platform))

    @classmethod
    def diff_filename(cls, platform):
        return os.path.join(cls.out_dir(), 'diff', '{}-{}.png'.format(cls.__name__, platform))


    @classmethod
    def ensure_environment(cls):
        if hasattr(ScreenshotTest, '_loaded_environment'):
            return
        pebble_install_and_run(PLATFORMS)
        ensure_empty_dir(cls.out_dir())
        os.mkdir(os.path.join(cls.out_dir(), 'img'))
        os.mkdir(os.path.join(cls.out_dir(), 'diff'))
        ScreenshotTest.summary_file = SummaryFile(cls.summary_filename(), BASE_CONFIG)
        ScreenshotTest._loaded_environment = True

    def test_screenshot(self):
        if not hasattr(self, 'config'):
            self.config = {}
        if not hasattr(self, 'sgvs'):
            self.sgvs = []

        self.ensure_environment()
        set_sgvs(self.sgvs)
        set_config(dict(BASE_CONFIG, nightscout_url=MOCK_HOST, **self.config), PLATFORMS)

        fails = []
        for platform in PLATFORMS:
            pebble_screenshot(self.test_filename(platform), platform)

            try:
                os.stat(self.gold_filename(platform))
            except OSError:
                images_match = False
                reason = 'Test is missing "gold" image: {}'.format(self.gold_filename(platform))
            else:
                images_match = image_diff(self.test_filename(platform), self.gold_filename(platform), self.diff_filename(platform))
                reason = 'Screenshot does not match expected: "{}"'.format(self.__class__.__doc__)
                reason += '\n' + self.circleci_url() if self.circleci_url() else ''

            ScreenshotTest.summary_file.add_test_result(self, platform, images_match)
            if not images_match:
                fails.append((platform, reason))

        assert fails == [], '\n'.join(['{}: {}'.format(p, reason) for p, reason in fails])


class SummaryFile(object):
    """Generate summary file with screenshots, in a very janky way for now."""
    def __init__(self, out_file, base_config):
        self.out_file = out_file
        self.base_config = base_config
        self.fails = ''
        self.passes = ''

    def write(self):
        with open(self.out_file, 'w') as f:
            f.write("""
            <head>
              <style>
                td { border: 1px solid #666; padding: 4px; vertical-align: top; }
                table { border-collapse: collapse; margin-bottom: 2em; }
                img.pass { border: 5px solid #aea; }
                img.fail { border: 5px solid red; }
                code { display: block; border-top: 1px solid #999; margin-top: 0.5em; padding-top: 0.5em; }
              </style>
            </head>
            <body>
            """
            +
            """
              <table>
                {fails}
              </table>
              <table>
                {passes}
              </table>
            """.format(fails=self.fails, passes=self.passes))

            f.write("""
            <strong>Default config</strong> (each test's config is merged into this):
            <br>
            <code>{}</code>
            """.format(json.dumps(self.base_config)))

    def add_test_result(self, test_instance, platform, passed):
        result = """
        <tr>
          <td><img src="{test_filename}" class="{klass}"></td>
          <td><img src="{diff_filename}"></td>
          <td>
            <strong>{classname} [{platform}]</strong> {doc}
            <code>{config}</code>
            <code>{sgvs}</code>
          </td>
        </tr>
        """.format(
            test_filename=self.relative_path(test_instance.test_filename(platform)),
            klass=('pass' if passed else 'fail'),
            diff_filename=self.relative_path(test_instance.diff_filename(platform)),
            classname=test_instance.__class__.__name__,
            platform=platform,
            doc=test_instance.__class__.__doc__ or '',
            config=json.dumps(test_instance.config),
            sgvs=json.dumps(self.printed_sgvs(test_instance.sgvs))
        )
        if passed:
            self.passes += result
        else:
            self.fails += result
        self.write()

    def relative_path(self, filename):
        return os.path.relpath(filename, os.path.dirname(self.out_file))

    def printed_sgvs(self, sgvs):
        return [
            s
            if i == 0
            else {
                'sgv': s.get('sgv'),
                'ago': self.format_ago(s['date'])
            }
            for i, s in enumerate(sgvs)
        ]

    @staticmethod
    def format_ago(time):
        now = int(datetime.now().strftime('%s'))
        minutes = int(round((now - time / 1000) / 60))
        if minutes < 60:
            return '{}m'.format(minutes)
        else:
            return '{}h{}m'.format(minutes / 60, minutes % 60)
