from dataiku.project_standards import (
    ProjectStandardsCheckRunResult,
    ProjectStandardsCheckSpec,
)


class MyProjectStandardsCheckSpec(ProjectStandardsCheckSpec):
    """
    Write your own logic by modifying the body of the run() method.

    .. important::
        This class will be automatically instantiated by DSS, do not add a custom constructor on it.

    The superclass is setting those fields for you:
    self.config: the dict of the configuration of the object
    self.plugin_config: the dict of the plugin settings
    self.project: the current `DSSProject` to use in your check spec
    self.original_project_key: the project key of the original project

    .. note::
        self.project.project_key and self.original_project_key may differ because Project Standards is sometimes run on a copy of the project.
        This temporary project is created solely for running checks and will be deleted afterward.
    """

    def run(self):
        """
        Run the check

        :returns: the run result.
            Use `ProjectStandardsCheckRunResult.success(message)` or `ProjectStandardsCheckRunResult.failure(severity, message)` depending on the result.
            Use `ProjectStandardsCheckRunResult.not_applicable(message)` if the check is not applicable to the project.
            Use `ProjectStandardsCheckRunResult.error(message)` if you want to mark the check as an error. You can also raise an Exception.
        """

        parameter2 = self.config["parameter2"]  # use self.config to get your check config values
        project = self.project  # use self.project to access the current project
        if len(project.get_summary()["name"]) <= parameter2:
            return ProjectStandardsCheckRunResult.success("Project name is small. limit={0}".format(parameter2))
        else:
            return ProjectStandardsCheckRunResult.failure(3, "Project name is too long. limit={0}".format(parameter2))
