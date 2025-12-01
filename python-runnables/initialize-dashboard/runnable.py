from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet
from dataiku.runnables import Runnable, ResultTable
from dataikupulse.src import dss_folder
from dataikupulse.src import dss_funcs
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
        self.preset_pc = dss_funcs.get_preset_pc("DATAIKU-PULSE", {})
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
        instance_name = dss_funcs.get_dss_name(self.local_client)
        project_handle = self.local_client.get_project(self.params["pulse_project_key"])
        library = project_handle.get_library()
                
        # Create the folders
        if cont:
            try:
                f = dss_folder.get_local_folder(self, project_handle, "partitioned_data")
                results.append(["Create Folders", True, None])
            except Exception as e:
                results.append(["Create Folders", False, f"An error occurred: {e}"])
                cont = False

        # Get plugin directory
        if cont:
            root_path = self.local_client.get_instance_info().raw["dataDirPath"]
            source_path = None
            path_install = f"{root_path}/plugins/installed/dataiku-pulse"
            path_dev = f"{root_path}/plugins/dev/dataiku-pulse"
            if os.path.isdir(path_install):
                source_path = path_install
                results.append(["plugin directory found", True, None])
            elif os.path.isdir(path_dev):
                source_path = path_dev
                results.append(["plugin directory found", True, None])
            else:
                results.append(["plugin directory", False, "Cannot find plugin Directory"])
                cont = False

        # Create the Code Studio Template
        if cont:
            try:
                found = False
                for cs in project_handle.list_code_studios():
                    if cs.name == "Dataiku Pulse Dashboard":
                        found = True
                        cs_id = cs.id
                        break
                if not found:
                    code_studio = project_handle.create_code_studio(name="Dataiku Pulse Dashboard", template_id="dataiku_pulse_dashboard")
                    cs_id = code_studio.code_studio_id
                results.append(["Create Code Studio", True, None])
            except Exception as e:
                results.append(["Create Code Studio", False, f"An error occurred: {e}"])
                cont = False
        
        # Get Code Studio directory
        if cont:
            code_studio_path = f"{root_path}/config/projects/{self.params['pulse_project_key']}/code_studios/{cs_id}"
            if not os.path.isdir(code_studio_path):
                results.append(["Project Library Confirmed", False, f"Cannot find project library {code_studio_path}"])
                cont = False
            else:
                results.append(["Project Library Confirmed", True, None])
        
        # Delete the current running version
        if cont:
            streamlit_path = f"{code_studio_path}/dataiku_pulse"
            if os.path.exists(streamlit_path) and os.path.isdir(streamlit_path):
                try:
                    shutil.rmtree(streamlit_path)
                    results.append(["Delete Existing", True, None])
                except OSError as e:
                    results.append(["Delete Existing", False, f"Error deleting directory '{streamlit_path}': {e}"])
                    cont = False
            else:
                results.append(["Delete Existing", True, "Initial Setup"])
        
        # Copy the streamlit application
        if cont:
            try:
                r = shutil.copytree(f"{source_path}/streamlit", streamlit_path)
                results.append(["Copy Streamlit", True, None])
            except Exception as e:
                results.append(["Copy Streamlit", False, f"An error occurred: {e}"])
                cont = False
                
        # 
        if cont and self.params.get("", False):
            varaibels = project_handle.get_variables()
            salt, ciphertext = encrypt_string("Hello World!", "DF2!&sEkm)f4}i99,e&9bS:Wj")
        
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
