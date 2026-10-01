#!/bin/bash
# Clone the 20 assigned packages + 8 replacements into repos/<owner>_<name>.
# Full history is needed (scan.py reads every commit). ~1 GB total, ~1 min.
cd "$(dirname "$0")" && mkdir -p repos && cd repos
while read r; do
  [ -z "$r" ] && continue
  d=$(echo "$r" | tr / _)
  [ -d "$d" ] || git clone -q -c core.longpaths=true "https://github.com/$r.git" "$d" && echo "ok $r"
done <<'EOF'
encode/django-rest-framework
wagtail/wagtail
pennersr/django-allauth
django-cms/django-cms
carltongibson/django-filter
jazzband/django-debug-toolbar
django-extensions/django-extensions
django-haystack/django-haystack
django-crispy-forms/django-crispy-forms
django-guardian/django-guardian
jazzband/django-taggit
jschneier/django-storages
django-oscar/django-oscar
cookiecutter/cookiecutter-django
django-import-export/django-import-export
deschler/django-modeltranslation
django/channels
graphql-python/graphene-django
django-tenants/django-tenants
jazzband/django-two-factor-auth
yourlabs/django-autocomplete-light
iMerica/dj-rest-auth
jazzband/django-silk
django-tastypie/django-tastypie
jazzband/django-oauth-toolkit
sunscrapers/djoser
django-commons/django-simple-history
mbi/django-rosetta
EOF
