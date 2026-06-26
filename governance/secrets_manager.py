import os
from dotenv import load_dotenv

load_dotenv()


class SecretsManager:

    @staticmethod
    def get_secret(key):

        value = os.getenv(key)

        if not value:
            raise Exception(f"Missing secret: {key}")

        return value