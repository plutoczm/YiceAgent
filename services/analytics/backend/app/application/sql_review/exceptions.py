class SQLReviewError(Exception):
    pass


class SQLReviewParseError(SQLReviewError):
    pass


class SQLReviewGenerationError(SQLReviewError):
    pass
