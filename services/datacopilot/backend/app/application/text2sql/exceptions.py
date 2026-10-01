class Text2SQLError(Exception):
    pass


class SchemaParseError(Text2SQLError):
    pass


class SQLGenerationError(Text2SQLError):
    pass


class SQLValidationError(Text2SQLError):
    pass
