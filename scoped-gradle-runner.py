#!/usr/bin/python
import re
import os
import subprocess

# obtains required inputs
source_branch='origin/' + os.getenv('BITRISEIO_GIT_BRANCH_DEST')
target_branch='origin/' + os.getenv('BITRISE_GIT_BRANCH')

project_location = os.getenv('project_location')
gradle_scope = os.getenv('gradle_scope')
gradle_task = os.getenv('gradle_task')
gradle_options = os.getenv('gradle_options')
gradlew_path = project_location + '/gradlew'

print('\nConfigs:', flush=True)
print('\t{0}: {1}'.format('project_location', project_location), flush=True)
print('\t{0}: {1}'.format('gradle_scope', gradle_scope), flush=True)
print('\t{0}: {1}'.format('gradle_task', gradle_task), flush=True)
print('\t{0}: {1}'.format('gradle_options', gradle_options), flush=True)

# variables definition
was_executed_previosly = "was_executed_previosly"

changed_files = "changed_files"
changed_modules = "changed_modules"
impacted_modules = "impacted_modules"
all_modules = "all_modules"

scopes = {
    changed_files: set(),
    changed_modules: set(),
    impacted_modules: set(),
    all_modules: set(),
}

def main():
    # sets execution directory
    os.chdir(project_location)

    build_scopes_map()

    cmd = build_command()
    if(cmd is not None):
        print('\n\nGradle command:\n{0}\n'.format(cmd), flush=True)
        subprocess.run(cmd, shell=True, check=True)
    else:
        print('There were no tasks to be executed in the selected scope.', flush=True)

def build_command():
    tasks = ""
    if(gradle_scope == changed_modules):
        tasks = build_tasks(scopes[changed_modules])
    elif(gradle_scope == impacted_modules):
        tasks = build_tasks(scopes[impacted_modules])
    elif(gradle_scope == all_modules):
        tasks = build_tasks(scopes[all_modules])

    if(len(tasks) == 0):
        return None
    else:
        return './{0} {1} {2}'.format('gradlew', tasks, gradle_options)

def build_tasks(scoped_modules):
    available_tasks = get_available_tasks()
    print(available_tasks)
    tasks = ''
    for module in scoped_modules:
        module = module.replace('/', ':')
        task = module + ':' + gradle_task
        if(contains_task(task, available_tasks)):
            tasks += ':' + task + ' '
        else:
            print('Task {0} does not exist.'.format(task), flush=True)
    
    return tasks

def get_available_tasks():
    cmd = "./gradlew tasks --all | grep {0}".format(gradle_task)
    result = subprocess.check_output(cmd, shell=True)
    return result.decode('utf-8')

def contains_task(task, available_tasks):
    return task in available_tasks

def build_scopes_map():
    print(flush=True)
    if(os.getenv(was_executed_previosly) is not None):
        print('Changes have been evaluated before.', flush=True)
        print('Extracting changes from env vars.', flush=True)
        extract_previosly_evaluated_changes_from_env()
    else:
        print('Changes have not been evaluated before.', flush=True)
        print('Evaluating changes...', flush=True)

        evaluate_changes()

        set_env(was_executed_previosly, True)
        set_env(changed_files, repr(scopes[changed_files]))
        set_env(changed_modules, repr(scopes[changed_modules]))
        set_env(impacted_modules, repr(scopes[impacted_modules]))
        set_env(all_modules, repr(scopes[all_modules]))

    print('\n{0}: {1}'.format(changed_files, scopes[changed_files]))
    print('\n{0}: {1}'.format(changed_modules, scopes[changed_modules]))
    print('\n{0}: {1}'.format(impacted_modules, scopes[impacted_modules]))
    print('\n{0}: {1}'.format(all_modules, scopes[all_modules]))

def extract_previosly_evaluated_changes_from_env():
    extract_previosly_evaluated_scope_from_env(changed_files)
    extract_previosly_evaluated_scope_from_env(changed_modules)
    extract_previosly_evaluated_scope_from_env(impacted_modules)
    extract_previosly_evaluated_scope_from_env(all_modules)

def extract_previosly_evaluated_scope_from_env(scope):
    changes = os.getenv(scope)
    changes = eval(changes)
    scopes[scope].update(changes)

def build_changed_files_scope():
    # Parse git diff to retrieve all changed files
    diff = os.popen('git diff --name-only {0}..{1}'.format(target_branch, source_branch))
    diff = diff.read()
    diff = diff.splitlines()

    scopes[changed_files].update(diff)

def build_all_modules_scope():
    # Parse settings.gradle to retrieve all project's modules
    settings_gradle = 'settings.gradle'
    double_quoted = '("([^"]|"")*")'
    single_quoted = "('([^']|'')*')"
    regex = re.compile('({0}|{1})'.format(single_quoted, double_quoted))
    with open(settings_gradle, 'r') as settings:
        for line in settings:
            matches = regex.finditer(line)
            for match in matches:
                module = match.group(0)
                module = module[2:-1]
                module = module.replace(':', '/')
                scopes[all_modules].add(module)
    
def evaluate_changes():
    build_all_modules_scope()

    build_changed_files_scope()

    # Check which modules were changed by checking if
    # the changed files belongs to any of the modules.
    #
    # If a file belongs to no module, then it belongs to
    # the root project and can potentially impact in all
    # the modules.
    has_changes_on_root = False
    for file in scopes[changed_files]:
        is_on_module = False
        for module in scopes[all_modules]:
            if(file.startswith(project_location + '/' + module)):
                scopes[changed_modules].add(module)
                is_on_module = True

        if(is_on_module is False):
            has_changes_on_root = True
            scopes[impacted_modules].update(scopes[all_modules])

    scopes[impacted_modules].update(scopes[changed_modules])

    # Parses each modules build.gradle to extract its
    # dependencies. Only dependencies on modules in the
    # same project matter.
    #
    # Dependencies dict is inverted, which means that if
    # :a depends on :b, the dict should be equals to 
    # {'b': ['a']}
    #
    # This allows better performance in our algorithm.
    dependencies = {}
    if(has_changes_on_root is False):
        dependency_pattern = re.compile('^\s*implementation project.*')

        for module in scopes[all_modules]:
            module_path = module + '/build.gradle'
            with open(module_path, 'r') as build:
                for line in build:
                    match = dependency_pattern.search(line)
                    if(match):
                        dependency = extract_module(line)
                        if(dependency not in dependencies.keys()):
                            dependencies[dependency] = []

                        dependencies[dependency].append(module)

    # Iterates throw modules to mark modules that depends
    # on changed modules as impacted.
    stack = set()
    stack.update(scopes[changed_modules])
    while len(stack) > 0:
        dependency = stack.pop()
        if(dependency in dependencies.keys()):
            for module in dependencies[dependency]:
                if(module not in stack):
                    stack.add(module)
                    scopes[impacted_modules].add(module)

def set_env(key, value):
    key = "SCOPED_GRADLE_RUNNER_" + key.upper()
    cmd = 'envman add --key {0} --value "{1}"'.format(key, value)
    subprocess.run(cmd, shell=True, check=True)

def extract_module(line):
    single_quoted = re.compile("(?<=')[^']+(?=')")
    double_quoted = re.compile("(?<=\")[^']+(?=\")")

    module = single_quoted.search(line)
    if(module is None):
        module = double_quoted.search(line)

    module = module.group(0)
    module = module[1:]
    module = module.replace(':', '/')
    return module

main()