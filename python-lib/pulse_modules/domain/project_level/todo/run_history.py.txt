import pandas as pd


def main(self, project_handle, client_d = {}):
    cols = [
        "project_key",
        'scenario_id',
        'scenario_name',
        'run_id',
        'run_outcome',
        'run_start_time',
        'run_end_time',
        'run_duration',
        'step_name',
        'step_outcome',
        'step_error_message'
    ]
    df = pd.DataFrame(columns=cols)
    for scenario in project_handle.list_scenarios():
        scenario_payload = project_handle.get_scenario(scenario['id'])
        if not scenario_payload.get_settings().active:
            continue
        data = []
        for run in scenario_payload.get_last_runs():
            try:
                a = run.end_time
            except:
                continue # scenario still running
            for stepRuns in run.get_details()['stepRuns']:
                step_name = stepRuns['step']['name']
                # Get the step result -- SUCESS, FAILED, ABORTED
                if 'result' in stepRuns:
                    result = stepRuns['result']
                elif 'result' in stepRuns['scenarioRun']:
                    result = stepRuns['scenarioRun']['result']
                else:
                    # omg its running there are no results lmfao
                    continue
                step_pass = result['outcome']
                # Check for Error Messages
                err_msg = None
                if step_pass != 'SUCCESS':
                    if 'thrown' in result:
                        err_msg = result['thrown']['message']
                    else:
                        for r in stepRuns['additionalReportItems']:
                            if 'thrown' in r:
                                err_msg = r['thrown']['message']
                # Append Data
                data.append([
                    project_handle.project_key,
                    scenario['id'],
                    scenario['name'],
                    run.id,
                    run.outcome,
                    run.get_start_time(),
                    run.get_end_time(),
                    run.get_duration(),
                    step_name,
                    step_pass,
                    err_msg
                ])
        if df.empty:
            df = tdf = pd.DataFrame(data, columns=cols)
        else:
            df = pd.concat([df, tdf], ignore_index=True)
    return df