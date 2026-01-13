META = {
    "id": "users.git_commits_top_committers",
    "version": 1,
    "label": "Top Git Committers",
    "description": "Users with the highest number of git commits per instance",
    "type": "graph",
    "tab": "activity",
    "graph": {
        "kind": "bar",
        "x": "login",
        "y": "commit_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "User",
        "y_title": "Git Commits",
        "legend_title": "Instance",
        "labels": {
            "login": "User Login",
            "commit_count": "Git Commits",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        WITH commits AS (
            SELECT
                instance_name,
                login
            FROM users_git_history_base
        ),
        top_committers AS (
            SELECT
                login
            FROM commits
            GROUP BY login
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        SELECT
            c.instance_name,
            c.login,
            COUNT(*) AS commit_count
        FROM commits c
        JOIN top_committers t
            ON c.login = t.login
        GROUP BY
            c.instance_name,
            c.login
        ORDER BY
            commit_count DESC,
            c.instance_name
    ;
    """
