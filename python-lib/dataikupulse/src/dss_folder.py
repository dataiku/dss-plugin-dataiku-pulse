# dataikupulse/src/dss_folder.py
## Last Modified: 2025-11-21
# -----------------------------------------------------------------------------------------
import dataiku
import pandas as pd
import io


# ---------- DATAIKU LOCAL FOLDERS --------------------------------------------------------
def create_local_folder(self, project_handle, folder_name):
    folder_handle = project_handle.create_managed_folder(
        name = folder_name,
        connection_name = self.params["pulse_folder_connection"]
    )
    if folder_name == "partitioned_data":
        settings = folder_handle.get_settings()
        settings.remove_partitioning()
        settings.add_discrete_partitioning_dimension("instance_name")
        settings.add_discrete_partitioning_dimension("category")
        settings.add_discrete_partitioning_dimension("module")
        settings.add_time_partitioning_dimension("date", period='DAY')
        settings.set_partitioning_file_pattern("%{instance_name}/%{category}/%{module}/%Y/%M/%D/.*")
        settings.save()
    return


def get_local_folder(self, project_handle, folder_name):
    folder = dataiku.Folder(
        lookup = folder_name,
        project_key = self.params["pulse_project_key"],
        ignore_flow = True
    )
    try:
        folder.get_id()
    except:
        try:
            folder = create_local_folder(self, project_handle, folder_name)
        except Exception as e:
            raise Exception(e)
    return folder


def update_datetime(df):
    for column in df.columns:
        if is_datetime(df[column]):
            try:
                min_date = df[df[column] != "1970-01-01"][column].min()
                df.loc[df[column] == "1970-01-01", column] = min_date
            except:
                pass
    return df


def read_local_folder_input(self, project_handle, folder_name, path):
    folder = get_local_folder(self, project_handle, folder_name)
    with folder.get_download_stream(path) as stream:
        file_bytes = io.BytesIO(stream.read())
    df = pd.read_parquet(file_bytes)
    return df


def write_local_folder_output(self, project_handle, folder_name, path, df):
    folder = get_local_folder(self, project_handle, folder_name)
    f = io.BytesIO()
    df.to_parquet(f, compression="gzip", engine='pyarrow', index=False)
    f.seek(0)
    content = f.read()
    folder.upload_stream(path, content)
    return


# ---------- DATAIKU REMOTE FOLDERS --------------------------------------------------------
def write_remote_folder_output(self, path, df):
    project_handle = self.remote_client.get_project(project_key=self.params["pulse_project_key"])
    fid = None
    for f in project_handle.list_managed_folders():
        if f["name"] == "partitioned_data":
            fid = f["id"]
            break
    if not fid:
        raise Exception()
    folder = project_handle.get_managed_folder(odb_id=fid)
    r = folder.put_file(path, df.to_parquet(compression="gzip", engine='pyarrow', index=False))
    return