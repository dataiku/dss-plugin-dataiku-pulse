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
                for code_studios in project_handle.list_code_studios(): # lets delete the existing if found
                    if code_studios.name == "Dataiku Pulse Dashboard":
                        code_studios_handle = project_handle.get_code_studio(code_studio_id=code_studios.id)
                        code_studios_handle.delete()
                code_studio = project_handle.create_code_studio(name="Dataiku Pulse Dashboard", template_id="dataiku_pulse_dashboard")
                cs_id = code_studio.code_studio_id
                results.append(["Create Code Studio", True, None])
            except Exception as e:
                results.append(["Create Code Studio", False, f"An error occurred: {e}"])
                cont = False
        
        # Get Code Studio directory
        if cont:
            code_studio_path = f"{root_path}/config/projects/{self.params['pulse_project_key']}/code_studios/{cs_id}"
            streamlit_path = f"{code_studio_path}/dataiku_pulse"
            if os.path.isdir(code_studio_path):
                results.append(["Project Library Confirmed", True, None])
            else:
                results.append(["Project Library Confirmed", False, f"Cannot find project library {code_studio_path}"])
                cont = False

        # Copy the streamlit application
        if cont:
            try:
                r = shutil.copytree(f"{source_path}/streamlit", streamlit_path)
                results.append(["Copy Streamlit", True, None])
            except Exception as e:
                results.append(["Copy Streamlit", False, f"An error occurred: {e}"])
                cont = False
                
        # Google Cloud HMAC Key
        if cont and self.params.get("connection_gcs", False):
            try:
                salt, ciphertext = encrypt_string(self.params["gcp_hmac_secret"], "DF2!&sEkm)f4}i99,e&9bS:Wj")
                variables = project_handle.get_variables()
                variables["local"]["gcs_hmac"] = {
                    "salt": base64.b64encode(salt).decode(),
                    "ciphertext": base64.b64encode(ciphertext).decode(),
                    "access_key": self.params["gcp_hmac_key"]
                }
                project_handle.set_variables(variables)
                results.append(["Store Encrypted HMAC", True, None])
            except Exception as e:
                results.append(["Store Encrypted HMAC", False, f"An error occurred: {e}"])
                cont = False