# Configuration file for the Sphinx documentation builder.

import os
import sys

# Add the path to the tik_manager4 folder to sys.path
sys.path.insert(0, os.path.abspath('../../python/ddrig/'))

# Import the version from _version.py
from _version import __version__

# -- Project information

project = 'DDRIG'
copyright = '2024-2026, Drafter'
author = 'Drafter'

# Automatically populate version and release from _version.py
version = __version__  # Short X.Y version
release = __version__  # Full version including alpha/beta/rc tags


# release = '1'
# version = '2.6.5'

# -- General configuration

extensions = [
    'sphinx.ext.duration',
    'sphinx.ext.doctest',
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.intersphinx',
    'sphinx_toolbox.collapse',
    'autoapi.extension',
]

autoapi_dirs = ['../../python/ddrig/']
autoapi_type = 'python'
autoapi_ignore = ['*setup*', '*shiboken*', '*PySide2*', '*PySide6*', '*PyQt5*', '*PyQt6*']
autoapi_file_patterns = ['*.py']
add_module_names = False
autoapi_member_order = 'groupwise'
autoapi_python_use_implicit_namespaces = True
autodoc_typehints = "signature"
autoapi_options = [ 'members', 'undoc-members', 'show-inheritance', 'show-module-summary', 'imported-members', ]

intersphinx_mapping = {
    'python': ('https://docs.python.org/3/', None),
    'sphinx': ('https://www.sphinx-doc.org/en/master/', None),
}
intersphinx_disabled_domains = ['std']

templates_path = ['_templates']

# -- Options for HTML output

html_theme = 'sphinx_rtd_theme'
html_theme_options = {
    'navigation_depth': 8,  # Set the desired TOC depth
}

# -- Options for EPUB output
epub_show_urls = 'footnote'
