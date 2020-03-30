"""
Send syslog messages to Sentry as events.
"""

import sys
import enum
import logging
import argparse

import syslog_rfc5424_parser

import sentry_sdk
from sentry_sdk.integrations import atexit
from sentry_sdk.integrations import dedupe
from sentry_sdk.integrations import logging as sentry_logging


# Manage version through the VCS CI/CD process
try:
    from . import version
except ImportError:  # pragma: no cover
    version = None
if version is not None:  # pragma: no cover
    __version__ = version.version


class SyslogSeverityToPythonLevel(enum.IntEnum):
    """
    Map syslog severities to Python's logging levels.
    """

    emerg = logging.CRITICAL
    alert = logging.CRITICAL
    crit = logging.CRITICAL
    err = logging.ERROR
    warning = logging.WARNING
    notice = logging.WARNING
    info = logging.INFO
    debug = logging.DEBUG


def logging_level_type(level_name):
    """
    Lookup the logging level corresponding to the named level.
    """
    try:
        level = getattr(logging, level_name)
    except Exception as exc:
        raise argparse.ArgumentTypeError(
            "Could not look up logging level from name:\n{}".format(exc.args[0])
        )
    if not isinstance(level, int):
        raise argparse.ArgumentTypeError(
            "Level name {!r} doesn't correspond to a logging level, got {!r}".format(
                level_name, level
            )
        )

    looked_up_level_name = logging.getLevelName(level)
    if looked_up_level_name != level_name:
        raise argparse.ArgumentTypeError(
            (
                "Looked up logging level {!r} "
                "doesn't match the given level name {!r}"
            ).format(level, level_name)
        )

    return level


# Define command line options and arguments
parser = argparse.ArgumentParser(description=__doc__.strip())
parser.add_argument(
    "--input-file",
    "-i",
    type=argparse.FileType("r"),
    default=sys.stdin,
    help="Take the syslog messages from this file, one per-line. (default: stdin)",
)
parser.add_argument(
    "--event-level",
    "-e",
    type=logging_level_type,
    default=logging.ERROR,
    help=(
        "Capture log messages of this level and above as Sentry events.  "
        "All other events are captured as Sentry breadcrumbs. "
        "(default: %(default)s)"
    ),
)
parser.add_argument(
    "sentry_dsn", help=("The DSN for your sentry DSN or client key."),
)


def run(
    input_file=parser.get_default("input_file"),
    event_level=parser.get_default("event_level"),
):
    """
    The inner loop for sending syslog lines as events and breadcrumbs to Sentry.

    Expects the Sentry Python logging integration to be initialized before being
    called.
    """
    for syslog_line in input_file:
        syslog_msg = syslog_rfc5424_parser.SyslogMessage.parse(syslog_line[:-1])
        syslog_msg_dict = syslog_msg.as_dict()

        level = getattr(SyslogSeverityToPythonLevel, syslog_msg.severity.name).value
        args = ()
        kwargs = {}
        syslog_fields = {
            key: value
            for key, value in syslog_msg_dict.items()
            if value is not None
            and value != {}
            and value != []
            and key not in {"facility", "appname", "severity", "msg", "version"}
        }
        if level >= event_level:
            # For Sentry events, the event["logentry"]["params"] key seems to be the
            # best user experience in the UI
            args = (syslog_fields,)
        else:
            # For Sentry breadcrumbs, log record args are ignored and only extra is
            # included
            kwargs = dict(extra=syslog_fields)

        logging.getLogger("{facility}.{appname}".format(**syslog_msg_dict)).log(
            level, syslog_msg.msg, *args, **kwargs
        )


def process_syslog_fields(event, hint):
    """
    Move syslog fields not handled by the logging integration as appropriate.
    """
    event["platform"] = "syslog"
    event["server_name"] = event["logentry"]["params"].pop(
        "hostname", event["server_name"]
    )
    event["timestamp"] = event["logentry"]["params"].pop(
        "timestamp", event["timestamp"]
    )

    for breadcrumb in event.get("breadcrumbs", []):
        breadcrumb["timestamp"] = breadcrumb["data"].pop(
            "timestamp", breadcrumb["timestamp"]
        )

    return event


def main(args=None):
    args = parser.parse_args(args=args)

    atexit_integration = atexit.AtexitIntegration()
    dedupe_integration = dedupe.DedupeIntegration()
    logging.getLogger().setLevel(level=logging.NOTSET)
    logging_integration = sentry_logging.LoggingIntegration(
        event_level=args.event_level
    )
    sentry_sdk.init(
        dsn=args.sentry_dsn,
        default_integrations=False,
        integrations=[atexit_integration, dedupe_integration, logging_integration],
        before_send=process_syslog_fields,
    )

    kwargs = {
        arg: value for arg, value in vars(args).items() if arg not in {"sentry_dsn"}
    }
    with args.input_file:
        return run(**kwargs)


main.__doc__ = __doc__


if __name__ == "__main__":  # pragma: no cover
    main()
