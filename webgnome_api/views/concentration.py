"""
Views for the Spill objects.
"""
from cornice import Service

from webgnome_api.common.views import (cors_policy, create_object, get_object,
                                       update_object)

concentration = Service(name='concentration', path='/concentration*obj_id',
                description="Concentration API", cors_policy=cors_policy)

implemented_types = ('gnome.concentration.concentration.Concentration',
                     )


@concentration.get()
def get_concentration(request):
    '''Returns a Gnome Spill object in JSON.'''
    return get_object(request, implemented_types)


@concentration.post()
def create_concentration(request):
    '''Creates a Gnome Spill object.'''
    return create_object(request, implemented_types)


@concentration.put()
def update_concentration(request):
    '''Updates a Gnome Spill object.'''
    return update_object(request, implemented_types)
