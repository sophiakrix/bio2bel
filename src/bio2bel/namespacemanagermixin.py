# -*- coding: utf-8 -*-

"""Provide abstractions over BEL namespace generation procedures"""

import logging
from abc import abstractmethod

import time
from pybel.manager.models import Namespace
from sqlalchemy import and_
from tqdm import tqdm

from .abstractmanager import AbstractManager
from .cli_utils import add_cli_to_bel_namespace

log = logging.getLogger(__name__)

__all__ = [
    'Bio2BELMissingNamespaceModelError',
    'NamespaceManagerMixin',
]


class Bio2BELMissingNamespaceModelError(TypeError):
    """Raised when the namespace_model class variable is not defined"""


class NamespaceManagerMixin(AbstractManager):
    """This mixin adds functions for making a BEL namespace to a repository

    *How to Use This Mixin*

    1. Either include it as a second inheriting class after :class:`AbstractManager` (this is how mixins are usually
    used):

    ..code-block:: python


        from bio2bel import AbstractManager
        from bio2bel.namespacemanagermixin import NamespaceManagerMixin

        class MyManager(AbstractManager, NamespaceManagerMixin):
            ...


    1. Or subclass it directly, since it also inherits from :class:`AbstractManager`, like:

    ..code-block:: python

        from bio2bel.namespacemanagermixin import NamespaceManagerMixin

        class MyManager(NamespaceManagerMixin):
            ...

    """
    namespace_model = ...

    def __init__(self, *args, **kwargs):
        """
        :param Optional[str] connection: SQLAlchemy connection string
        """
        if self.namespace_model is ...:
            raise Bio2BELMissingNamespaceModelError

        super().__init__(*args, **kwargs)

    @abstractmethod
    def _create_namespace_entry_from_model(self, model, namespace=None):
        """"""

    @abstractmethod
    def _get_identifier(self, model):
        """Given an instance of namespace_model, extract its identifier"""
        pass

    def _get_namespace_entries(self):
        return [
            self._create_namespace_entry_from_model(model)
            for model in self._iterate_namespace_models()
        ]

    def _iterate_namespace_models(self):
        """Return an iterator over the models to be converted to the namespace"""
        return tqdm(self._get_query(self.namespace_model), total=self._count_model(self.namespace_model))

    @classmethod
    def _get_namespace_keyword(cls):
        """Gets the keyword to use as the reference BEL namespace.

        :rtype: str
        """
        return '_{}'.format(cls.module_name.upper())

    @classmethod
    def _get_namespace_filter(cls):
        """Get an SQLAlchemy filter for getting the reference BEL namespace.

        :return:
        """

        _namespace_keyword = cls._get_namespace_keyword()

        return and_(
            Namespace.keyword == _namespace_keyword,
            Namespace.url == _namespace_keyword
        )

    def _get_default_namespace(self):
        """Get the reference BEL namespace if it exists.

        :rtype: Optional[pybel.manager.models.Namespace
        """

        namespace_filter = self._get_namespace_filter()
        return self._get_query(Namespace).filter(namespace_filter).one_or_none()

    def _make_namespace(self):
        """
        :rtype: pybel.manager.models.Namespace
        """
        from pybel.manager.models import Namespace

        entries = self._get_namespace_entries()
        _namespace_keyword = self._get_namespace_keyword()
        ns = Namespace(
            name=_namespace_keyword,
            keyword=_namespace_keyword,
            url=_namespace_keyword,
            version=str(time.asctime()),
            entries=entries,
        )
        self.session.add(ns)

        t = time.time()
        log.info('committing models')
        self.session.commit()
        log.info('committed models in %.2f seconds', time.time() - t)

        return ns

    @staticmethod
    def _build_old_entry_set(namespace):
        """Converts PyBEL generalized namespace entries to a set.

        Default to using the identifier, but can be overridden to use the name instead.

        :rtype: set[pybel.manager.model.NamespaceEntry]

        >>> {term.identifier for term in namespace.entries}
        """
        return {term.identifier for term in namespace.entries}

    def _update_namespace(self):
        """Only call this if namespace won't be none!

        :rtype: pybel.manager.models.Namespace
        """
        namespace = self._get_default_namespace()

        old_entry_set = self._build_old_entry_set(namespace)
        new_count = 0

        for model in self._iterate_namespace_models():
            if self._get_identifier(model) in old_entry_set:
                continue

            new_count += 1
            entry = self._create_namespace_entry_from_model(model, namespace=namespace)
            self.session.add(entry)

        t = time.time()
        log.info('got %d new entries. committing models', new_count)
        self.session.commit()
        log.info('committed models in %.2f seconds', time.time() - t)

        return namespace

    def upload_bel_namespace(self):
        """
        :rtype: pybel.manager.models.Namespace
        """
        if not self.is_populated():
            self.populate()

        ns = self._get_default_namespace()

        if ns is None:
            return self._make_namespace()

        return self._update_namespace()


    @classmethod
    def get_cli(cls):
        """Gets a :mod:`click` main function to use as a command line interface."""
        main = super().get_cli()
        add_cli_to_bel_namespace(main)
        return main