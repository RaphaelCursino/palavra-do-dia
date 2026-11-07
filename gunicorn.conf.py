import os


preload_app = False
os.environ["PALAVRA_DO_DIA_PROCESS_ROLE"] = "master"


def post_fork(_server, _worker):
    os.environ["PALAVRA_DO_DIA_PROCESS_ROLE"] = "worker"
