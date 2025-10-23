import os, json
from dataiku.code_studio import CodeStudioBlock

class MyCodeStudioBlock(CodeStudioBlock):
    def __init__(self, config, plugin_config):
        """
        :param config: the dict of the configuration of the object
        :param plugin_config: contains the plugin settings
        """
        self.config = config
        self.plugin_config = plugin_config
        
    def build_spec(self, spec, env):
        """
        Apply the block to the spec
        
        :param spec: the current state of the CodeStudio template image spec
        :param env: the build env

        :returns: the updated spec, ie a dict with a field 'dockerfile'
        """
        dockerfile = spec.get("dockerfile", "")
        dockerfile = dockerfile + "\n\n# this was added by a block\n"
        return {"dockerfile":dockerfile}

    def build_launch(self, spec, env):
        """
        Apply the block to the spec
        
        :param spec: the current state of the CodeStudio launch spec
        :param env: the launch env

        :returns: the updated spec, as a dict
        """
        yaml = spec.get("yaml", "")
        yaml = yaml + "\n\n# this was added by a block\n"
        result = dict(spec)
        result["yaml"] = yaml
        return result
