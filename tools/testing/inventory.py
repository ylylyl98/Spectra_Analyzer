"""Source and declared unittest inventory; never imports test modules."""
import ast
import hashlib
from pathlib import Path

def ast_inventory(root):
    modules = []
    for source in sorted((root / 'tests').rglob('test*.py')):
        name = '.'.join(source.relative_to(root).with_suffix('').parts)
        entry = dict(module=name, path=str(source), sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                     top_class_ids=[], top_function_ids=[], all_nested_declarations=[], parse_error=None)
        try:
            tree = ast.parse(source.read_text(encoding='utf-8-sig'))
            entry['top_function_ids'] = [name + '.' + node.name for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_')]
            for cls in [node for node in tree.body if isinstance(node, ast.ClassDef)]:
                entry['top_class_ids'].extend(name + '.' + cls.name + '.' + method.name
                    for method in cls.body if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith('test_'))
            def walk(node, parents=()):
                for child in ast.iter_child_nodes(node):
                    names = parents
                    if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        names = (*parents, child.name)
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith('test_'):
                            entry['all_nested_declarations'].append(name + '.' + '.'.join(names))
                    walk(child, names)
            walk(tree)
        except Exception as error:
            entry['parse_error'] = repr(error)
        modules.append(entry)
    return modules


def source_inventory(root):
    """Hash production, test, QML and source tooling, excluding runtime outputs."""
    paths = {p for p in root.iterdir() if p.is_file() and p.suffix in ('.py', '.qml')}
    for name in ('spectra_app', 'tests', 'tools'):
        folder = root / name
        if folder.is_dir():
            paths.update(p for p in folder.rglob('*') if p.is_file() and p.suffix in ('.py', '.qml')
                         and '__pycache__' not in p.relative_to(root).parts)
    return [dict(path=p.relative_to(root).as_posix(), size_bytes=p.stat().st_size,
                 sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(paths)]


def declared_child_variant(repo, variant, manifest):
    """Accept only the explicitly declared, unchanged child selector contract."""
    entry = next(e for e in manifest if e['module'] == variant['module'])
    source = Path(entry['path']).read_text(encoding='utf-8-sig')
    tree = ast.parse(source)
    expected_flag = ast.parse("_CHILD = os.environ.get(" + repr(variant['flag']) + ") == '1'").body[0]
    if not any(ast.dump(node) == ast.dump(expected_flag) for node in tree.body):
        raise ValueError('Declared child environment selector changed')
    selector = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'load_tests')
    assignment = next(node for node in selector.body if isinstance(node, ast.Assign))
    expression = assignment.value
    if not (len(assignment.targets) == 1 and isinstance(assignment.targets[0], ast.Name)
            and assignment.targets[0].id == 'case' and isinstance(expression, ast.IfExp)
            and isinstance(expression.test, ast.Name) and expression.test.id == '_CHILD'
            and isinstance(expression.body, ast.Name) and expression.body.id == variant['case_class']):
        raise ValueError('Declared child load_tests selector changed')
    expected_return = ast.parse('return loader.loadTestsFromTestCase(case)').body[0]
    if not any(ast.dump(node) == ast.dump(expected_return) for node in selector.body):
        raise ValueError('Declared child load_tests return changed')
    ids = [case for case in entry['top_class_ids']
           if case.startswith(variant['module'] + '.' + variant['case_class'] + '.')]
    if len(ids) != variant['expected_cases']:
        raise ValueError('Declared child case count changed: ' + str(len(ids)))
    return ids
