from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet
from dataiku.runnables import Runnable, ResultTable
from dataikupulse.src import dss_folder
from dataikupulse.src import dss_funcs
from datetime import datetime
import base64
import os
import pandas as pd
import shutil
import logging


def derive_key_from_password(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def encrypt_string(plaintext: str, password: str) -> tuple[bytes, bytes]:
    salt = os.urandom(16)  # store this with the ciphertext
    key = derive_key_from_password(password, salt)
    f = Fernet(key)
    ciphertext = f.encrypt(plaintext.encode())
    return salt, ciphertext


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config
        self.params = plugin_config.get("pulse_primary", {})
        self.local_client = dss_funcs.build_local_client()
        self.remote_client = dss_funcs.build_remote_client(self)
        self.dt = datetime.utcnow()
        
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)
        self.logger = logging.getLogger(__name__)
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):      
        results = []
        cont = True
        
        # Get local client and name
        instance_name = dss_funcs.get_dss_name(self)
        project_handle = self.local_client.get_project(self.params["pulse_project_key"])
        library = project_handle.get_library()
                
        # Create the folders
        if cont:
            for folder in ["partitioned_data", "gold_tables"]:
                try:
                    f = dss_folder.get_local_folder(self, project_handle, folder)
                    results.append([f"Create Folders - {folder}", True, None])
                except Exception as e:
                    results.append([f"Create Folders - {folder}", False, f"An error occurred: {e}"])
                    cont = False
        
        # Create the gold recipe
        if cont:
            create_recipe = False
            try:
                exists = project_handle.get_recipe(recipe_name="generate_gold_tables")
                exists.get_settings()
            except:
                create_recipe = True
            
            if create_recipe:
                folder = get_local_folder(self, project_handle, "gold_tables")
                folder_id = folder.get_id()
                recipe_handle = project_handle.create_recipe(
                    recipe_proto={
                        'type': 'CustomCode_create-gold-tables',
                        'name': 'generate_gold_tables'
                    },
                    creation_settings={}
                )
                settings = recipe.get_settings()
                settings.add_output(role="gold_tables_folder", ref=folder_id)
                settings.save()
                    
        # return results
        if results:
            df = pd.DataFrame(results, columns=["step", "result", "message"])
            df = df.astype(str)
            rt = ResultTable()
            n = 1
            for col in df.columns:
                rt.add_column(n, col, "STRING")
                n +=1
            for index, row in df.iterrows():
                rt.add_record(row.tolist())
            return rt
        else:
            raise Exception("Something went wrong")
