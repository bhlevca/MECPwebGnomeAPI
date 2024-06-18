"""
Views for the Model object.
"""
import logging
import os
import shutil
import subprocess
from threading import current_thread

import gnome.scripting as gs
import ujson
from cornice import Service
from gnome.model import Model
from pyramid.httpexceptions import (HTTPBadRequest, HTTPNotFound,
                                    HTTPNotImplemented,
                                    HTTPUnsupportedMediaType)
from pyramid.response import Response

from webgnome_api.common.common_object import (CreateObject, UpdateObject,
                                               clean_session_dir,
                                               obj_id_from_req_payload,
                                               obj_id_from_url)
from webgnome_api.common.helpers import JSONImplementsOneOf
from webgnome_api.common.session_management import (acquire_session_lock,
                                                    get_active_model,
                                                    get_session_object,
                                                    get_session_objects,
                                                    init_session_objects,
                                                    set_active_model,
                                                    set_session_object)
from webgnome_api.common.views import (cors_exception, cors_policy,
                                       cors_response, gen_unique_filename,
                                       get_object, switch_to_existing_session,
                                       web_ser_opts)
from webgnome_api.views.mover import create_mover
from webgnome_api.views.outputter import update_outputter

from ..common.common_object import get_persistent_dir, get_session_dir
from ..common.system_resources import write_to_file

log = logging.getLogger(__name__)
model = Service(name='model', path='/model*obj_id', description="Model API",
                cors_policy=cors_policy)

mikehd = Service(name='mikehd', path='/mikehd', description="MIKE HD API",
                 cors_policy=cors_policy)

mikehdnetcdf = Service(name='mikehdnetcdf', path='/mikehdnetcdf', description="MIKE HD API",
                       cors_policy=cors_policy)

mikehdstatus = Service(name='mikehdstatus', path='/mikehdstatus', description="MIKE HD API",
                 cors_policy=cors_policy)

implemented_types = ('gnome.model.Model',
                     )


def create_mike_hd_config(request):
    """
    Create MIKE HD Model Workflow Config file

    Parameters
    ----------
    request: web request

    """
    # get model
    my_model = get_active_model(request)

    # create the json file with three values
    config = {}
    config['SimulationStart'] = my_model.start_time.strftime(
        "%Y-%m-%d %H:%M:%S")
    config['SimulationEnd'] = (
        my_model.start_time +
        my_model.duration).strftime("%Y-%m-%d %H:%M:%S")
    config['LakeName'] = my_model.lake

    config_path = r'C:\temp\jsonconfig'
    if not os.path.exists(config_path):
        os.makedirs(config_path)
    with open(os.path.join(config_path, 'Config.json'), 'w') as f:
        f.write(ujson.dumps(config))

    return my_model.lake


mike_hd_status_file = r'C:\temp\webgnome_mike_hd_status.txt'
mike_hd_result_folder = r'C:\temp\HD Model Setup\Current\Results'
mike_hd_result_netcdf_folder = os.path.join(mike_hd_result_folder,'netCDF')

def update_hd_status_file(code):
    """
    Update MIKE HD model status with given code

    Parameters
    ----------
    code: the MIKE HD model status code
    1 - model is running
    0 - model runs successfully
    -1 - model runs with error
    -2 - status file doesn't exist

    """
    with open(mike_hd_status_file, 'w') as f:
        f.write(str(code))


def get_hd_status():
    """
    Read MIKE HD model status code

    Returns
    -------
    code: the MIKE HD model status code
    1 - model is running
    0 - model runs successfully
    -1 - model runs with error
    -2 - status file doesn't exist

    """
    if os.path.exists(mike_hd_status_file) and os.path.isfile(
            mike_hd_status_file):
        with open(mike_hd_status_file, 'r') as f:
            try:
                return int(f.read())
            except Exception:
                return -1
    else:
        return -2


def run_hd(lake):
    """
    Trigger HD Model Run

    Returns
    -------
    0: Model Started
    1: Model is running
    2: Can't find WebGNOME_DIR in Environment Variables
    3: Can't the WorkflowExecuter or Workflow JSON file

    """
    if get_hd_status() == 1:
        return 1, "One model is running. Please wait."

    ev_webgnome_dir = 'WebGNOME_DIR'
    if ev_webgnome_dir in os.environ:
        webgnome_dir = os.environ[ev_webgnome_dir]

        wf_designer = os.path.join(
            webgnome_dir,
            "designer",
            "DHI.WorkflowExecuter.exe")
        wf = os.path.join(
            webgnome_dir,
            "workflows",
            "webgnome_workflows.json")
        if os.path.exists(wf_designer) and os.path.exists(wf):
            cmd = f'"{wf_designer}" -run local -filename "{wf}" -workflowid "Run HD Model - {lake}"'
            update_hd_status_file(1)
            subprocess.Popen(cmd)
            return 0, "Model Started"
        else:
            return 3, "Can't the WorkflowExecuter or Workflow JSON file"
    else:
        return 2, "Can't find WebGNOME_DIR in Environment Variables"

def copy_netcdf(request):
    return copy_file(request, mike_hd_result_netcdf_folder,".nc")

def copy_dfsu(request, appendix):
    return copy_file(request,mike_hd_result_folder, "2D all.dfsu", appendix)

def copy_file(request, folder, extension, appendix = -1):
    """
    Copy the resulting NetCDF file to the session folder

    Parameters
    ----------
    request: web request

    Returns
    -------
    filename: the file name of the copied netcdf file
    file_path: the file pat of the copied netcdf file


    Remarks
    -------
    borrowed from upload_manager

    """
    if os.path.isdir(folder) and os.path.exists(folder):
        nc_files = [x for x in os.listdir(folder) if extension in x]
        if len(nc_files) > 0:
            fn = nc_files[0]            
            input_file = os.path.join(folder, fn)
            upload_dir = os.path.relpath(get_session_dir(request))
            file_name, unique_name, appendix = gen_unique_filename(
                fn, upload_dir, appendix)
            file_path = os.path.join(upload_dir, unique_name)
            write_to_file(input_file, file_path)

            return unique_name, file_path, appendix
        else:
            return "", ""

    return "", ""


@mikehd.get()
def run_mikehd(request):
    '''
        run mike hd model
    '''
    lake = create_mike_hd_config(request)
    code, drescription = run_hd(lake)
    return cors_response(request, Response(ujson.dumps(
        {'code': code, 'description': drescription})))

@mikehdstatus.get()
def retrieve_hd_status(request):
    '''
        get hd status
    '''
    status = get_hd_status()
    return cors_response(request, Response(ujson.dumps(
        {'status': status})))


@mikehdnetcdf.post()
def get_mikehd_netcdf(request):
    """
    Create a new mover with MIKE HD Model NetCDF file

    """
    # this is important to allow change to existing session
    switch_to_existing_session(request)

    # check model status
    status = get_hd_status()
    if status == 1 or status == -1:
        return cors_response(request, Response(
            ujson.dumps({'error_code': status})))        

    # copy netcdf and 2D all.dfsu to session folder
    file_name, filepath, appendix = copy_netcdf(request)   

    #create DfsuWaterDepth
    copy_dfsu(request, appendix)

    # create the mover
    # borrow from upload_mover
    if len(file_name) > 0 and os.path.exists(filepath):
        mover_type = 'gnome.movers.py_current_movers.CurrentMover'
        name = 'MIKE HD'
        basic_json = {'obj_type': mover_type,
                      'filename': filepath,
                      'name': name}

        env_obj_base_json = {'obj_type': 'temp',
                             'name': name,
                             'data_file': filepath,
                             'grid_file': filepath,
                             'grid': {'obj_type': ('gnome.environment.'
                                                   'gridded_objects_base.PyGrid'),
                                      'filename': filepath}
                             }

        if ('py_current_movers.CurrentMover' in mover_type):
            env_obj_base_json['obj_type'] = ('gnome.environment'
                                             '.environment_objects.GridCurrent')
            basic_json['current'] = env_obj_base_json
        request.body = ujson.dumps(basic_json).encode('utf-8')

        mover_obj = create_mover(request)
        resp = Response(ujson.dumps(mover_obj))

        return cors_response(request, resp)
    else:
        # netcdf file is not ready
        return cors_response(request, Response(
            ujson.dumps({'error_code': 0})))


@model.get()
def get_model(request):
    '''
        Returns Model object in JSON.
        - This method varies slightly from the common object method in that
          if we don't specify a model ID, we:
          - return the current active model if it exists or...
          - return the specification.
    '''
    ret = None
    obj_id = obj_id_from_url(request)

    session_lock = acquire_session_lock(request)
    log.info('  session lock acquired (sess:{}, thr_id: {})'
             .format(id(session_lock), current_thread().ident))

    try:
        if obj_id is None:
            my_model = get_active_model(request)
            if my_model is not None:
                ret = my_model.serialize(options=web_ser_opts)

        if ret is None:
            ret = get_object(request, implemented_types)
    finally:
        session_lock.release()
        log.info('  session lock released (sess:{}, thr_id: {})'
                 .format(id(session_lock), current_thread().ident))

    return ret


@model.post()
def create_model(request):
    '''
        Creates a new model
    '''
    log_prefix = 'req({0}): create_object():'.format(id(request))
    log.info('>>' + log_prefix)

    try:
        json_request = ujson.loads(request.body.decode('utf-8'))
    except Exception:
        json_request = None

    if json_request and not JSONImplementsOneOf(json_request,
                                                implemented_types):
        raise cors_exception(request, HTTPNotImplemented)

    session_lock = acquire_session_lock(request)
    log.info('  {} session lock acquired (sess:{}, thr_id: {})'
             .format(log_prefix, id(session_lock), current_thread().ident))

    try:
        clean_session_dir(request)
        init_session_objects(request, force=True)

        if json_request:
            new_model = CreateObject(json_request,
                                     get_session_objects(request))
        else:
            new_model = Model()

        set_session_object(new_model, request)

        set_active_model(request, new_model.id)
    except Exception:
        raise cors_exception(request, HTTPUnsupportedMediaType,
                             with_stacktrace=True)
    finally:
        session_lock.release()
        log.info('  {} session lock released (sess:{}, thr_id: {})'
                 .format(log_prefix, id(session_lock), current_thread().ident))

    log.info('<<' + log_prefix)
    return new_model.serialize(options=web_ser_opts)


@model.put()
def update_model(request):
    '''
        Returns Model object in JSON.
        - This method varies slightly from the common object method in that
          if we don't specify a model ID, we:
          - update the current active model if it exists or...
          - generate a 'Not Found' exception.
    '''
    log_prefix = 'req({0}): update_model():'.format(id(request))
    log.info('>>' + log_prefix)

    ret = None
    try:
        json_request = ujson.loads(request.body.decode('utf-8'))
    except Exception:
        raise cors_exception(request, HTTPBadRequest)

    if not JSONImplementsOneOf(json_request, implemented_types):
        raise cors_exception(request, HTTPNotImplemented)

    session_lock = acquire_session_lock(request)
    log.info('  {} session lock acquired (sess: {}, thr_id: {})'
             .format(log_prefix, id(session_lock), current_thread().ident))

    obj_id = obj_id_from_req_payload(json_request)
    if obj_id:
        active_model = get_session_object(obj_id, request)
    else:
        active_model = get_active_model(request)

    if active_model:
        try:
            if UpdateObject(active_model, json_request,
                            get_session_objects(request)):
                set_session_object(active_model, request)
            ret = active_model.serialize(options=web_ser_opts)
        except Exception:
            raise cors_exception(request, HTTPUnsupportedMediaType,
                                 with_stacktrace=True)
        finally:
            session_lock.release()
            log.info(f'  {log_prefix} session lock released...')
    else:
        session_lock.release()
        log.info('  {} session lock released (sess:{}, thr_id: {})'
                 .format(log_prefix, id(session_lock), current_thread().ident))

        msg = ("raising cors_exception() in update_model. "
               "Updating model before it exists.")
        log.warning('  ' + log_prefix + msg)

        raise cors_exception(request, HTTPNotFound)

    log.info('<<' + log_prefix)
    return ret


@model.delete()
def delete_object(request):
    '''
        THIS IS INCOMPLETE DO NOT USE
        Deletes the object specified by the 'id' in the request.
        Removes all references to the object in the active model and all
        component objects, using recursive search.
        Also removes object pool references, and sets the name to signify
        the object is deleted
    '''
    log_prefix = 'req({0}): delete_object():'.format(id(request))
    log.info('>>' + log_prefix)
    session_lock = acquire_session_lock(request)
    obj_id = obj_id_from_req_payload(json_request)

    if obj_id:
        active_model = get_session_object(obj_id, request)
    else:
        raise cors_exception(
            request,
            HTTPBadRequest,
            explanation=f'Deletion target not found: {obj_id}'
        )
