import logging


class TokenFilter(logging.Filter):

    def filter(self, record):

        message = record.getMessage()

        if "token" in message.lower():
            record.msg = "[REDACTED TOKEN]"

        return True


def protect_logs():

    logging.getLogger().addFilter(TokenFilter())