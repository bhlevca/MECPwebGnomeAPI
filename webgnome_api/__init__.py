"""
    Main entry point
"""
import logging
import os
import shutil
from pathlib import Path

import gevent
import socketio
import ujson
from gevent import pywsgi
from geventwebsocket.handler import WebSocketHandler
from pyramid.config import Configurator
from pyramid.renderers import JSON as JSONRenderer
from pyramid.threadlocal import get_current_request
from pyramid_log import Formatter, _DottedLookup, _WrapDict
from pyramid_redis_sessions import session_factory_from_settings
from redis import StrictRedis

from webgnome_api.common.views import cors_policy
from webgnome_api.socket.sockserv import (WebgnomeNamespace,
                                          WebgnomeSocketioServer)

__version__ = "0.9"

logging.basicConfig()


class WebgnomeFormatter(Formatter):
    def format(self, record):
        # Format the specific record as text.
        has_session_hash = hasattr(record, 'session_hash')
        if not has_session_hash:
            record.session_hash = '<no session>'
            gvt = gevent.getcurrent()
            gvt_session_hash = (isinstance(gvt, gevent.Greenlet) and
                                hasattr(gvt, 'session_hash'))

            if gvt_session_hash:
                record.session_hash = gvt.session_hash
            else:
                request = get_current_request()

                if request is not None:
                    record.session_hash = request.session_hash

        # magic_record.__dict__ support dotted attribute lookup
        magic_record = _WrapDict(record, _DottedLookup)

        # Disable logging during disable to prevent recursion (in case
        # a logged request property generates a log message)
        save_disable = logging.root.manager.disable
        logging.disable(record.levelno)

        try:
            return logging.Formatter.format(self, magic_record)
        finally:
            logging.disable(save_disable)


class DummySession(object):
    session_id = 'DummySession'


def reconcile_directory_settings(settings):
    save_file_dir = settings['save_file_dir']

    for d in (save_file_dir,):
        if not os.path.exists(d):
            print(('Creating folder {0}'.format(d)))
            os.mkdir(d)
        elif not os.path.isdir(d):
            raise EnvironmentError('Folder path {0} '
                                   'is not a directory!!'.format(d))

    locations_dir = settings['locations_dir']

    if not os.path.exists(locations_dir):
        raise EnvironmentError('Location files folder path {0} '
                               'does not exist!!'.format(locations_dir))

    if not os.path.isdir(locations_dir):
        raise EnvironmentError('Location files folder path {0} '
                               'is not a directory!!'.format(locations_dir))


def load_cors_origins(settings, key):
    if key in settings:
        origins = settings[key].split('\n')
        cors_policy['origins'] = origins


def get_json(request):
    return ujson.loads(request.text, ensure_ascii=False)


def overload_redis_session_factory(settings, config):
    '''
        pyramid_redis_sessions will create a session object for every request,
        even the CORS preflight requests, and if there is no session cookie,
        a new session key will be created.  And the CORS preflight requests
        will never have a session cookie.  So we overload the session factory
        function here and add a special case for CORS preflight requests.
    '''
    session_factory = session_factory_from_settings(settings)

    def overloaded_session_factory(request, **kwargs):
        if request.method.lower() == 'options':
            return DummySession()
        else:
            return session_factory(request, **kwargs)

    config.set_session_factory(overloaded_session_factory)


def start_session_cleaner(settings):
    '''
        When a session expires, we need to cleanup the session folder that was
        created for it, but pyramid_redis_sessions has no builtin way to add
        or register custom functions to do this.
        So we need to hook directly into the Redis publish/subscribe
        functionality.  Here we will look for expired key events.
    '''
    host = settings.get('redis.sessions.host', 'localhost')
    port = int(settings.get('redis.sessions.port', 6379))
    session_dir = settings.get('session_dir', './models/session')

    redis = StrictRedis(host=host, port=port)

    def event_handler(msg, session_dir=session_dir):
        cleanup_dir = os.path.join(str(session_dir), str(msg['data']))

        try:
            shutil.rmtree(cleanup_dir)
        except OSError as err:
            # not-found error.  Print message & continue.
            if err.errno == 2:
                print('Session Cleaner: Folder {} does not exist!'
                      .format(cleanup_dir))
            else:
                raise

    pubsub = redis.pubsub()
    pubsub.psubscribe(**{'__keyevent*__:expired': event_handler})

    settings['redis_pubsub_thread'] = pubsub.run_in_thread(
        sleep_time=60.0, daemon=True)


def server_factory(global_config, host, port):
    port = int(port)

    def serve(app):
        # app is gzip middlware; app.application == webgnome_api
        sio = WebgnomeSocketioServer(
            app_settings=global_config,
            api_app=app.application,
            async_mode='gevent',
            # logger=True,
            # ping_interval=2,
            # ping_timeout=10
        )
        ns = WebgnomeNamespace('/')
        sio.register_namespace(ns)
        # to allow access to socketio side from pyramid side
        app.application.registry['sio_ns'] = ns
        # sio.register_namespace(LoggerNamespace('/logger'))
        app = socketio.WSGIApp(sio, app)
        pywsgi.WSGIServer((host, port), app,
                          handler_class=WebSocketHandler).serve_forever()
    return serve


def main(global_config, **settings):
    settings['package_root'] = os.path.abspath(
        os.path.dirname(__file__))
    settings['objects'] = {}

    settings['uncertain_models'] = {}
    try:
        os.mkdir('ipc_files')
    except OSError as e:
        # it is ok if the folder already exists.
        if e.errno != 17:
            raise

    reconcile_directory_settings(settings)
    load_cors_origins(settings, 'cors_policy.origins')
    start_session_cleaner(settings)

    config = Configurator(settings=settings)

    overload_redis_session_factory(settings, config)

    # we use ujson to load our JSON payloads
    config.add_request_method(get_json, 'json', reify=True)

    renderer = JSONRenderer(serializer=lambda v, **kw: ujson.dumps(v))
    config.add_renderer('json', renderer)

    config.add_tween('webgnome_api.tweens.PyGnomeSchemaTweenFactory')

    config.add_route('upload', '/upload')
    config.add_route('activate', '/activate')
    config.add_route('download', '/download')
    config.add_route('persist', '/persist')

    config.add_route('map_upload', '/map/upload')
    config.add_route('map_activate', '/map/activate')

    config.add_route('mover_upload', '/mover/upload')

    config.add_route('substance_upload', '/substance/upload')
    config.add_route('release_upload', '/release/upload')

    config.add_route('environment_upload', '/environment/upload')
    config.add_route('environment_activate', '/environment/activate')

    config.add_route('socket.io', '/socket.io/*remaining')
    config.add_route('logger', '/logger')

    config.scan('webgnome_api.views', ignore=[
        # 'webgnome_api.views.socket',
        # 'webgnome_api.views.socket_logger',
        # 'webgnome_api.views.socket_step'
    ])

    wapi = config.make_wsgi_app()  # pyramid object creates a wsgi app
    return wapi
