import re, logging


class LibLocationPathReplacer(object):
    def __init__(self, spec):
        self.spec = spec
        self.variable_to_zone = {}
        def find_zone(zones, *chunks):
            names = []
            names.append(''.join(chunks))
            names.append(''.join([c.lower() for c in chunks]))
            names.append('-'.join([c.lower() for c in chunks]))
            names.append('_'.join([c.lower() for c in chunks]))
            for zone in zones:
                for name in names:
                    if zone['zone'] == name:
                        return zone
            return None
        self.variable_to_zone['__CODE_STUDIO_VERSIONED__'] = find_zone(spec.get("syncedZones", []), 'code_studio', 'Versioned')
        self.variable_to_zone['__CODE_STUDIO_RESOURCES__'] = find_zone(spec.get("syncedZones", []), 'code_studio', 'Resources')
        self.variable_to_zone['__PROJECT_LIB_VERSIONED__'] = find_zone(spec.get("syncedZones", []), 'project', 'Lib', 'Versioned')
        self.variable_to_zone['__PROJECT_LIB_RESOURCES__'] = find_zone(spec.get("syncedZones", []), 'project', 'Lib', 'Resources')
        self.variable_to_zone['__USER_VERSIONED__'] = find_zone(spec.get("syncedZones", []), 'user', 'Versioned')
        self.variable_to_zone['__USER_RESOURCES__'] = find_zone(spec.get("syncedZones", []), 'user', 'Resources')
        self.variable_to_zone['__RECIPES__'] = find_zone(spec.get("syncedZones", []), 'recipes')
        
    def replace_variable_by_path(self, p):
        for variable, zone in self.variable_to_zone.items():
            if zone is None:
                continue
            location = zone.get('pathInContainer', '')
            if location != '':
                p = p.replace(variable, location)
        return p
                        
    def replace_path_by_variable(self, p):
        for variable, zone in self.variable_to_zone.items():
            if zone is None:
                continue
            location = zone.get('pathInContainer', '')
            if location != '':
                p = p.replace(location, variable)
        return p

    def clear_variables(self, p):
        for variable in self.variable_to_zone:
            p = p.replace(variable, '')
        return p
                        
def infer_base_url_per_port(env, port):
    url_with_port = env.get('baseUrlLNTPerPort', '')
    url_with_port = url_with_port.replace('${baseUrl}', env.get('baseUrlLNT', ''))
    url_with_port = url_with_port.replace('${exposedPort}', str(port))
    return url_with_port


# last provided package win
# merge_packages_strings("toto==1.0 tata>2", "toto>1 titi==3")
# => "toto>1 tata>2 titi==3"
def merge_packages_strings(*packages_str):
    packages_dic = {}
    for package_str in packages_str:
        for dep in re.split(r'\s+', package_str.strip()):
            match = re.search(r'^"?([^\s=><,]+)([^"]*)"?$', dep)
            if match:
                [lib,ver] = match.groups()
                packages_dic[lib.strip()] = ver.strip()
    return " ".join(['"' + k + packages_dic[k] + '"' for k in packages_dic])


# return a docker string to build a generated/dss/user codenv and its path
# used by the Streamlit and JupyterLab blocks (at least)
def generate_python_codenv(name, config, template,
                           default_packages="",
                           default_pyenv_path="/opt/dataiku/pyenv",
                           default_python_bin="python3.9",
                           code_env_settings={}):

    codenv_mode = config.get("codeEnvMode", "generate")
    generate_codenv = ""
    if codenv_mode == "generate":
        
        python_bin = config.get("pythonVersion", default_python_bin)
        base_packages = config.get("pythonPackages", default_packages)
        user_packages = config.get("pythonModules", "")
        packages = merge_packages_strings(base_packages, user_packages)
        generate_codenv = build_generate_python_codenv_script(name, packages, default_pyenv_path, python_bin, code_env_settings)
        pyenv_path = default_pyenv_path

    elif codenv_mode == "dss":

        pyenv_path = sniff_block_python_codenv_path(config, template)

    elif codenv_mode == "user":

        pyenv_path = config.get("userCodeEnvPath").strip()
        if not pyenv_path:
            raise ValueError("[" + name + "] You must specify the path of your code env")

    logging.info("[{}] codenv mode={} path={}".format(name, codenv_mode, pyenv_path))

    return generate_codenv, pyenv_path

def build_generate_python_codenv_script(name,
                           packages="",
                           pyenv_path="/opt/dataiku/pyenv",
                           python_bin="python3.9",
                           code_env_settings={}):

        
    venv_extra_options = code_env_settings.get("virtualenvCreateExtraOptions", [])
    pip_extra_options = code_env_settings.get("pipInstallExtraOptions", [])
    venv_extra_options = [x for x in venv_extra_options if x is not None and len(x.strip()) > 0 ]
    pip_extra_options = [x for x in pip_extra_options if x is not None and len(x.strip()) > 0 ]
    generate_codenv = """
# build {name} code env
RUN {python_bin} -m venv {venv_extra_options} {pyenv_path} \
&& (source {pyenv_path}/bin/activate && pip install {pip_extra_options} {packages})
""".format(name=name, pyenv_path=pyenv_path,
            packages=packages, python_bin=python_bin,
            venv_extra_options=' '.join(venv_extra_options),
            pip_extra_options=' '.join(pip_extra_options))

    return generate_codenv

def get_block_python_codenv_path(name, config, template, default_pyenv_path="/opt/dataiku/pyenv"):

    codenv_mode = config.get("codeEnvMode", "generate")
    if codenv_mode == "generate":
        pyenv_path = default_pyenv_path

    elif codenv_mode == "dss":

        pyenv_path = sniff_block_python_codenv_path(config, template)

    elif codenv_mode == "user":
        
        pyenv_path = config.get("userCodeEnvPath").strip()
        if not pyenv_path:
            raise ValueError("[" + name + "] You must specify the path of your code env")

    logging.info("[{}] codenv mode={} path={}".format(name, codenv_mode, pyenv_path))

    return pyenv_path

def sniff_block_python_codenv_path(config, template):

    dss_codenv = config.get("dssCodeEnv", "")
    blocks = template.get("params", {}).get("blocks", [])
    py_codenv_blocks = [b for b in blocks if b.get("type") == "add_codeenv" and b.get("params",{}).get("envLang") == "PYTHON"]
    if not dss_codenv:
        # blank = get first added python code env
        if len(py_codenv_blocks) == 0:
            raise ValueError("[" + name + "] You must have a Python code env. added to the template")
        dss_codenv = py_codenv_blocks[0].get("params",{}).get("envName")
    else:
        py_codenv_blocks = [b for b in py_codenv_blocks if b.get("params",{}).get("envName") == dss_codenv]
        if len(py_codenv_blocks) == 0:
            raise ValueError("[" + name + "] Unable to find a Python code env. named '{}', did you add the right code env block ?".format(dss_codenv))
    dss_codenv_block = py_codenv_blocks[0]
    # this envdir computation is copied from AddCodeEnvCodeStudioBlockMeta
    envdir = dss_codenv_block.get("params",{}).get("envDir","").strip()
    if not envdir:
        envdir = re.sub(r'[^a-zA-Z0-9-_]', '_', dss_codenv)
    pyenv_path = "/opt/dataiku/python-code-envs/" + envdir
    
    return pyenv_path

def sniff_jupyter_block(template):

    blocks = template.get("params", {}).get("blocks", [])
    jupyterlab_blocks = [b for b in blocks if b.get("type") == "pycdstdioblk_code-studio-blocks_jupyterlab"]
    if len(jupyterlab_blocks) == 0:
        return None
    else:
        return jupyterlab_blocks[0]

def python_version(str):
    version = re.search(r"([\d\.]+)", str)
    if version:
        return tuple(map(int, (version.group(1).split("."))))
    else:
        return None