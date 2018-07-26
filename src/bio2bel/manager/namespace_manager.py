# -*- coding: utf-8 -*-

"""Provide abstractions over BEL namespace generation procedures."""

import sys

import click
import logging
import time
from abc import ABC, abstractmethod
from tqdm import tqdm

from pybel.constants import NAMESPACE_DOMAIN_OTHER
from pybel.manager.models import Namespace
from pybel.resources import write_namespace
from .cli_manager import CliMixin
from .connection_manager import ConnectionManager

log = logging.getLogger(__name__)

__all__ = [
    'Bio2BELMissingNamespaceModelError',
    'BELNamespaceManagerMixin',
]


class Bio2BELMissingNamespaceModelError(TypeError):
    """Raised when the namespace_model class variable is not defined."""


class BELNamespaceManagerMixin(ABC, ConnectionManager, CliMixin):
    """This mixin adds functions for making a BEL namespace to a repository.

    *How to Use This Mixin*

    1. Either include it as a second inheriting class after :class:`AbstractManager` (this is how mixins are usually
    used):

    ..code-block:: python


        from bio2bel import AbstractManager
        from bio2bel.namespace_manager import NamespaceManagerMixin

        class MyManager(AbstractManager, NamespaceManagerMixin):
            ...


    1. Or subclass it directly, since it also inherits from :class:`AbstractManager`, like:

    ..code-block:: python

        from bio2bel.namespace_manager import NamespaceManagerMixin

        class MyManager(NamespaceManagerMixin):
            ...

    """
    namespace_model = ...

    identifiers_recommended = None
    identifiers_pattern = None
    identifiers_miriam = None
    identifiers_namespace = None
    identifiers_url = None

    def __init__(self, *args, **kwargs):
        if self.namespace_model is ...:
            raise Bio2BELMissingNamespaceModelError

        super().__init__(*args, **kwargs)

    @abstractmethod
    def _create_namespace_entry_from_model(self, model, namespace):
        """
        :param model: The model to convert
        :type namespace: pybel.manager.models.Namespace
        :rtype: Optional[pybel.manager.models.NamespaceEntry]
        """

    def _get_encoding(self, model):
        """Get the encoding for the model."""

    def _get_name(self, model):
        """Get the name for the namespace model."""

    @staticmethod
    @abstractmethod
    def _get_identifier(model):
        """Given an instance of namespace_model, extract its identifier.

        :param model: The model to convert
        :rtype: str
        """

    def _iterate_namespace_models(self):
        """Return an iterator over the models to be converted to the namespace"""
        return tqdm(
            self._get_query(self.namespace_model),
            total=self._count_model(self.namespace_model),
            desc='Mapping {} to BEL namespace'.format(self._get_namespace_name())
        )

    @classmethod
    def _get_namespace_name(cls):
        """Get the nicely formatted name of this namespace.

        :rtype: str
        """
        return cls.identifiers_recommended or cls.module_name

    @classmethod
    def _get_namespace_keyword(cls):
        """Get the keyword to use as the reference BEL namespace.

        :rtype: str
        """
        return cls.identifiers_namespace or cls.module_name.upper()

    @classmethod
    def _get_namespace_url(cls):
        """Get the URL to use as the reference BEL namespace.

        :rtype: str
        """
        return cls.identifiers_url or '_{}'.format(cls.module_name.upper())

    def _get_default_namespace(self):
        """Get the reference BEL namespace if it exists.

        :rtype: Optional[pybel.manager.models.Namespace]
        """
        return self._get_query(Namespace).filter(Namespace.url == self._get_namespace_url()).one_or_none()

    def _get_namespace_entries(self, namespace):
        return [
            namespace_entry
            for namespace_entry in (
                self._create_namespace_entry_from_model(model, namespace)
                for model in self._iterate_namespace_models()
            )
            if namespace_entry is not None
        ]

    def _make_namespace(self):
        """
        :rtype: pybel.manager.models.Namespace
        """
        namespace = Namespace(
            name=self._get_namespace_name(),
            keyword=self._get_namespace_keyword(),
            url=self._get_namespace_url(),
            version=str(time.asctime()),
        )
        self.session.add(namespace)

        entries = self._get_namespace_entries(namespace)
        self.session.add_all(entries)

        t = time.time()
        log.info('committing models')
        self.session.commit()
        log.info('committed models in %.2f seconds', time.time() - t)

        return namespace

    @staticmethod
    def _get_old_entry_identifiers(namespace):
        """Converts PyBEL generalized namespace entries to a set.

        Default to using the identifier, but can be overridden to use the name instead.

        :param pybel.manager.models.Namespace namespace:
        :rtype: set[pybel.manager.model.NamespaceEntry]

        >>> {term.identifier for term in namespace.entries}
        """
        return {term.identifier for term in namespace.entries}

    def _update_namespace(self, namespace):
        """Only call this if namespace won't be none!

        :type namespace: pybel.manager.models.Namespace
        """

        old_entry_identifiers = self._get_old_entry_identifiers(namespace)
        new_count = 0
        skip_count = 0

        for model in self._iterate_namespace_models():
            if self._get_identifier(model) in old_entry_identifiers:
                continue

            entry = self._create_namespace_entry_from_model(model, namespace=namespace)
            if entry is None or entry.name is None:
                skip_count += 1
                continue

            new_count += 1
            self.session.add(entry)

        t = time.time()
        log.info('got %d new entries. skipped %d entries missing names. committing models', new_count, skip_count)
        self.session.commit()
        log.info('committed models in %.2f seconds', time.time() - t)

    def upload_bel_namespace(self, update=False):
        """Upload the namespace to the PyBEL database.

        :param bool update: Should the namespace be updated first?
        :rtype: pybel.manager.models.Namespace
        """
        if not self.is_populated():
            self.populate()

        namespace = self._get_default_namespace()

        if namespace is None:
            log.info('making namespace for %s', self._get_namespace_name())
            return self._make_namespace()

        if update:
            self._update_namespace(namespace)

        return namespace

    def drop_bel_namespace(self):
        """Remove the default namespace if it exists.

        :rtype: Optional[pybel.manager.models.Namespace]
        """
        ns = self._get_default_namespace()

        if ns is not None:
            for entry in tqdm(ns.entries, desc='deleting entries in {}'.format(self._get_namespace_name())):
                self.session.delete(entry)
            self.session.delete(ns)

            log.info('committing deletions')
            self.session.commit()
            return ns

    def write_bel_namespace(self, file):
        """Write as a BEL namespace file.

        :param file:
        """
        values = {self._get_identifier(model) for model in self._iterate_namespace_models()}

        write_namespace(
            namespace_name=self._get_namespace_name(),
            namespace_keyword=self._get_namespace_keyword(),
            namespace_domain=NAMESPACE_DOMAIN_OTHER,
            namespace_query_url=self.identifiers_url,
            author_name='',
            citation_name='',
            values=values,
            functions='A',
            file=file,
        )

    @staticmethod
    def _cli_add_to_bel_namespace(main):
        """Add the export BEL namespace command.

        :type main: click.core.Group
        :rtype: click.core.Group
        """
        return add_cli_to_bel_namespace(main)

    @staticmethod
    def _cli_add_clear_bel_namespace(main):
        """Add the clear BEL namespace command.

        :type main: click.core.Group
        :rtype: click.core.Group
        """
        return add_cli_clear_bel_namespace(main)

    @staticmethod
    def _cli_add_write_bel_namespace(main):
        """Add the write BEL namespace command.

        :type main: click.core.Group
        :rtype: click.core.Group
        """
        return add_cli_write_bel_namespace(main)

    @classmethod
    def get_cli(cls):
        """Get a :mod:`click` main function to use as a command line interface.

        :rtype: click.core.Group
        """
        main = super().get_cli()

        @main.group()
        def belns():
            """Manage BEL namespace."""

        cls._cli_add_to_bel_namespace(belns)
        cls._cli_add_clear_bel_namespace(belns)
        cls._cli_add_write_bel_namespace(belns)

        return main


def add_cli_to_bel_namespace(main):
    """Add a ``upload_bel_namespace`` command to main :mod:`click` function.

    :param click.core.Group main: A click-decorated main function
    :rtype: click.core.Group
    """

    @main.command()
    @click.option('-u', '--update', is_flag=True)
    @click.pass_obj
    def upload(manager, update):
        """Upload names/identifiers to terminology store."""
        namespace = manager.upload_bel_namespace(update=update)
        click.echo('uploaded [{}] {}'.format(namespace.id, namespace.keyword))

    return main


def add_cli_clear_bel_namespace(main):
    """Add a ``clear_bel_namespace`` command to main :mod:`click` function.

    :param click.core.Group main: A click-decorated main function
    :rtype: click.core.Group
    """

    @main.command()
    @click.pass_obj
    def drop(manager):
        """Clear names/identifiers to terminology store."""
        namespace = manager.drop_bel_namespace()

        if namespace:
            click.echo('namespace {} was cleared'.format(namespace))

    return main


def add_cli_write_bel_namespace(main):
    """Add a ``write_bel_namespace`` command to main :mod:`click` function.

    :param click.core.Group main: A click-decorated main function
    :rtype: click.core.Group
    """

    @main.command()
    @click.option('-f', '--file', type=click.File('w'), default=sys.stdout)
    @click.pass_obj
    def write(manager, file):
        """Write a BEL namespace names/identifiers to terminology store."""
        manager.write_bel_namespace(file)

    return main
