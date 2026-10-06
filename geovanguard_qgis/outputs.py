"""Output destination helpers shared by the algorithms and the dialog."""
import os
import re


def destination_text(value):
    """Plain text of a sink parameter value (definition, property or string)."""
    sink = getattr(value, 'sink', None)
    if sink is not None:
        try:
            value = sink.staticValue()
        except AttributeError:
            pass
    return str(value or '').strip()


def is_temporary(value):
    """True for 'Create temporary layer' (or no destination at all)."""
    text = destination_text(value)
    return text in ('', 'TEMPORARY_OUTPUT') or text.startswith('memory:')


def output_file(value):
    """File path of a file destination (.gpkg, .shp, …), else None (temporary, database)."""
    text = destination_text(value)
    if is_temporary(text):
        return None
    m = re.search(r"dbname='([^']+)'", text)
    if m:
        path = m.group(1)
    elif ':' in text.split('/')[0].split('\\')[0] and not re.match(r'^[A-Za-z]:', text):
        return None                                   # provider URI, e.g. postgis:…
    else:
        path = text.split('|')[0]
    return path if os.path.splitext(path)[1] else None


def work_folder_for(value):
    """Work folder with the same name as the output file: E:/a/Forest.gpkg → E:/a/Forest."""
    path = output_file(value)
    return os.path.splitext(path)[0] if path else None
