def run_modules(self, mode = "instance", project_handle = None, client_d = {}, project_key = None):
    if mode == "instance":
        from dataikupulse.base_data import instance_level as dss_objs
    elif mode == "projects":
        from dataikupulse.base_data import project_level as dss_objs
    else:
        raise Exception("Unknown Module Mode")
    results = []
    directory = dss_objs.__path__[0]
    for root, _, files in os.walk(directory):
        for f in files:
            if not f.endswith(".py") or f == "__init__.py":
                continue
            module_name = f.removesuffix(".py")
            path = root.replace(directory, "")
            fp = os.path.join(root, f)
            category = path[1:]
            try:
                spec = importlib.util.spec_from_file_location(module_name, fp)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, 'main'):
                    if project_handle: # project level stuff
                        df = module.main(self, project_handle, client_d)
                    else:
                        df = module.main(self) # Instance level
                    results.append([project_key, category, module_name, "load/run", True, None])
            except Exception as e:
                df = pd.DataFrame()
                results.append([project_key, category, module_name, "load/run", False, e])
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue # nothing to write, skip
            # Save RAW Normalized to RAW layer
            dt_year  = str(self.dt.year)
            dt_month = str(f'{self.dt.month:02d}')
            dt_day   = str(f'{self.dt.day:02d}')
            file_name = "data.parquet" 
            if project_key:
                file_name = f"{project_key}_data.parquet"
            write_path = f"raw/{category}/{module_name}/{self.instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
            try:
                dss_folder.write_remote_folder_output(self, write_path, df)
                results.append([project_key, category, module_name, "write/save -- RAW", True, None])
            except Exception as e:
                results.append([project_key, category, module_name, "write/save -- RAW", False, e])
            # Test quality control -- Save to Silver or Raw Error
            FLAT_COLUMNS_BY_DOMAIN = load_flat_columns("schemas")
            layer = "silver"
            try:
                df = dss_silver.coerce_schema(df)
                dq = dss_silver.data_quality(df)
                df_report = pd.DataFrame([{
                    "errors": dq["errors"],
                    "warnings": dq["warnings"],
                    **dq["stats"],
                }])
                if dq["errors"]:
                    layer = "raw_errors"
                    write_path = f"{layer}/{category}/{module_name}/{self.instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                    dss_folder.write_remote_folder_output(self, write_path, df)
                    write_path = f"{layer}/{category}/{module_name}/{self.instance_name}/{dt_year}/{dt_month}/{dt_day}/dq_{file_name}"
                    dss_folder.write_remote_folder_output(self, write_path, df_report)
                    results.append([project_key, category, module_name, f"write/save -- {layer}", False, "Check raw errors"])
                else:
                    write_path = f"{layer}/{category}/{module_name}/{self.instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                    dss_folder.write_remote_folder_output(self, write_path, df)
                    results.append([project_key, category, module_name, f"write/save -- {layer}", True, None])  
            except Exception as e:
                results.append([project_key, category, module_name, f"write/save -- QUALITY", False, e])
    return results





# lets split the df by category and save
        for category, grp_df in merged_df.groupby("dataiku_category"):
            grp_df = grp_df.dropna(axis=1, how='all').reset_index(drop=True)
            # Get Flat Columns and Normalize
            FLAT_COLUMNS = event_flat_cols.get_flat_cols(category)
            grp_df = dss_silver.normalize_dataframe(self, grp_df, FLAT_COLUMNS)
            # Order the DF
            ordered = [c for c in FLAT_COLUMNS if c in grp_df.columns]
            rest = [c for c in grp_df.columns if c not in ordered]
            grp_df = grp_df[ordered + rest]
            try:
                file_name = f"data-{dt_epoch}.parquet" 
                write_path = f"raw/dataiku_usage/{category}/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                dss_folder.write_remote_folder_output(self, write_path, grp_df)
                results.append([f"write/save - Dataiku Usage {category}", True, f"data-{dt_epoch}.parquet"])
            except Exception as e:
                results.append([f"write/save - Dataiku Usage {category}", False, e])

            # Test quality control -- Save to Silver or Raw Error
            try:
                layer = "silver"
                grp_df = dss_silver.coerce_schema(grp_df)
                dq = dss_silver.data_quality(grp_df)
                if dq["errors"]:
                    layer = "raw_errors"
                    write_path = f"{layer}/dataiku_usage/{category}/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                    dss_folder.write_remote_folder_output(self, write_path, grp_df)
                    write_path = f"{layer}/dataiku_usage/{category}/{instance_name}/{dt_year}/{dt_month}/{dt_day}/dq_{file_name}"
                    dss_folder.write_remote_folder_output(self, write_path, pd.DataFrame(dq))
                else:
                    write_path = f"{layer}/dataiku_usage/{category}/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                    dss_folder.write_remote_folder_output(self, write_path, grp_df)
                results.append([f"write/save - Dataiku Usage {category} -- {layer}", True, None])
            except Exception as e:
                layer = "raw_errors"
                results.append([f"write/save - Dataiku Usage {category} -- QUALITY", False, e])