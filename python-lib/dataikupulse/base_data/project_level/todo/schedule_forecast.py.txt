import dataiku
import pandas as pd
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import calendar


def rel_month(start_dt, week_num, dow):
    # fix for correct day
    year, month = start_dt.year, start_dt.month
    c = calendar.Calendar(firstweekday=calendar.SUNDAY)
    monthcal = c.monthdatescalendar(year, month)
    day = [
        day for week in monthcal for day in week if \
        day.weekday() == dow and \
        day.month == month
    ][week_num]
    start_dt = start_dt.replace(day = day.day)
    return start_dt


def gather_forecast(key, scenario_id, params):
    # get initial start, and a rough ending
    start_dt   = f"{params['startingFrom']} {params['hour']}:{params['minute']:02d}"
    start_dt   = datetime.strptime(start_dt, "%Y-%m-%d %H:%M")
    current_dt = datetime.now()
    one_yr_dt  = current_dt + relativedelta(years=1)

    # Minute, Hour, Day, Week, Month
    data = []
    freq = params['repeatFrequency']

    if params['frequency']   == "Minutely":
        while current_dt >= start_dt:
            start_dt += timedelta(minutes=freq)
        start_dt -= timedelta(minutes=freq)
            
        while one_yr_dt > start_dt:
            data.append([key, scenario_id, start_dt])
            start_dt += timedelta(minutes=freq)

    elif params['frequency'] == "Hourly":
        while current_dt >= start_dt:
            start_dt += timedelta(hours=freq)
        start_dt -= timedelta(hours=freq)
        
        while one_yr_dt > start_dt:
            data.append([key, scenario_id, start_dt])
            start_dt += timedelta(hours=freq)

    elif params['frequency'] == "Daily":
        while current_dt >= start_dt:
            start_dt += timedelta(days=freq)
        start_dt -= timedelta(days=freq)
        
        while one_yr_dt > start_dt:
            data.append([key, scenario_id, start_dt])
            start_dt += timedelta(days=freq)

    elif params['frequency'] == "Weekly":
        while current_dt >= start_dt:
            start_dt += timedelta(weeks=freq)
        start_dt -= timedelta(weeks=freq)
        
        while one_yr_dt > start_dt:
            if "Monday" in params['daysOfWeek']:
                delta = 0 - start_dt.weekday()
                t = start_dt + timedelta(days=delta)
                data.append([key, scenario_id, t])
            if "Tuesday" in params['daysOfWeek']:
                delta = 1 - start_dt.weekday()
                t = start_dt + timedelta(days=delta)
                data.append([key, scenario_id, t])
            if "Wednesday" in params['daysOfWeek']:
                delta = 2 - start_dt.weekday()
                t = start_dt + timedelta(days=delta)
                data.append([key, scenario_id, t])
            if "Thursday" in params['daysOfWeek']:
                delta = 3 - start_dt.weekday()
                t = start_dt + timedelta(days=delta)
                data.append([key, scenario_id, t])
            if "Friday" in params['daysOfWeek']:
                delta = 4 - start_dt.weekday()
                t = start_dt + timedelta(days=delta)
                data.append([key, scenario_id, t])
            if "Saturday" in params['daysOfWeek']:
                delta = 5 - start_dt.weekday()
                t = start_dt + timedelta(days=delta)
                data.append([key, scenario_id, t])
            if "Sunday" in params['daysOfWeek']:
                delta = 6 - start_dt.weekday()
                t = start_dt + timedelta(days=delta)
                data.append([key, scenario_id, t])
            start_dt += timedelta(weeks=freq)

    elif params['frequency'] == "Monthly":
        if params["monthlyRunOn"] == 'ON_THE_DAY':
            while current_dt >= start_dt:
                start_dt += relativedelta(months=freq)
            start_dt -= relativedelta(months=freq)
        
            while one_yr_dt > start_dt:
                data.append([key, scenario_id, start_dt])
                start_dt += relativedelta(months=freq)
        else:
            dow = start_dt.weekday()
            c = calendar.Calendar(firstweekday=calendar.SUNDAY)
            year, month = start_dt.year, start_dt.month            
            monthcal = c.monthdatescalendar(year, month)
            weeks = [
                day for week in monthcal for day in week if \
                day.weekday() == dow and \
                day.month == month
            ]
            week_num = weeks.index(start_dt.date())
            if weeks[-1] == weeks[week_num]:
                week_num = -1
                
            while current_dt >= start_dt:
                start_dt += relativedelta(months=freq)
                start_dt = rel_month(start_dt, week_num, dow)
                
            # Lets go back 1
            start_dt -= relativedelta(months=freq)
            start_dt = rel_month(start_dt, week_num, dow)

            while one_yr_dt > start_dt:
                data.append([key, scenario_id, start_dt])
                start_dt += relativedelta(months=freq)
                start_dt = rel_month(start_dt, week_num, dow)
    else:
        raise Exception()

    return data


def main(self, project_handle, client_d = {}):
    schedule_data = []
    for scenario in project_handle.list_scenarios():
        if not scenario["active"]:
            continue
        scenario_handle = project_handle.get_scenario(scenario_id=scenario["id"])
        settings_settings = scenario_handle.get_settings()
        for trigger in settings_settings.raw_triggers:
            if trigger['type'] != "temporal":
                continue
            params = trigger['params']
            data = gather_forecast(project_handle.project_key, scenario["id"], params)
            schedule_data += data

    df = pd.DataFrame(
        schedule_data,
        columns=["project_key", "scenario_id", "next_run"]
    )
    
    return df