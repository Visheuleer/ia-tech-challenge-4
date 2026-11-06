class ApplicationError(Exception):
    pass


class RecordNotFound(ApplicationError):
    pass


class RecordConflict(ApplicationError):
    pass


class InvalidRelationship(ApplicationError):
    pass
