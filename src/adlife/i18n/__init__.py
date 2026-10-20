"""Guide translations: packaged catalogs, shared formatting and message references.

The guided presenter pages and the HTML run report are translated on the server from
the same catalogs the browser loads, so every language shows the same sentences. This
package depends only on the standard library, pydantic and markupsafe (plus the core
``DomainModel`` base), and the simulation core never imports it.
"""

from adlife.i18n.catalog import (
    Catalog,
    CatalogError,
    PluralMessage,
    catalog_bytes,
    catalog_parity_problems,
    load_catalog,
)
from adlife.i18n.formatting import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    Language,
    TextDirection,
    parse_language,
    plural_category,
    text_direction,
)
from adlife.i18n.jinja import template_functions
from adlife.i18n.refs import (
    ClockParam,
    IdParam,
    IntParam,
    ListParam,
    MessageRef,
    NumParam,
    PctParam,
    PersonLabel,
    PersonParam,
    RefContext,
    RefParam,
    TextParam,
    clock_param,
    id_param,
    int_param,
    list_param,
    message_ref,
    num_param,
    pct_param,
    person_param,
    text_param,
)

__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "Catalog",
    "CatalogError",
    "ClockParam",
    "IdParam",
    "IntParam",
    "Language",
    "ListParam",
    "MessageRef",
    "NumParam",
    "PctParam",
    "PersonLabel",
    "PersonParam",
    "PluralMessage",
    "RefContext",
    "RefParam",
    "TextDirection",
    "TextParam",
    "catalog_bytes",
    "catalog_parity_problems",
    "clock_param",
    "id_param",
    "int_param",
    "list_param",
    "load_catalog",
    "message_ref",
    "num_param",
    "parse_language",
    "pct_param",
    "person_param",
    "plural_category",
    "template_functions",
    "text_direction",
    "text_param",
]
