from . import EventideSharedProfiles


def getMetaData():
    return {}


def register(app):
    return {"extension": EventideSharedProfiles.EventideSharedProfiles()}
