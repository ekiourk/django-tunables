# django-tunables

Runtime-tunable parameters for Django with a full audit trail. A host project declares
a catalogue of parameters in Python; operators change values through the Django admin
or a REST API. Every change is an atomic, versioned change set, and every version
produces a materialized snapshot: one JSON document holding the complete effective
parameter set, ready for other processes to read. Each group can also be described as
JSON Schema plus a UI schema so external clients can render their own forms.

```
pip install django-tunables
```
