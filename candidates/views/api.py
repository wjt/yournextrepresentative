from __future__ import unicode_literals

import json
from os.path import dirname
import subprocess
import sys
from datetime import date, timedelta

import django
from django.contrib.auth.models import User
from django.db.models import Count, Prefetch
from django.http import HttpResponse
from django.views.generic import View

from rest_framework.reverse import reverse
from rest_framework.viewsets import ViewSet
from rest_framework.response import Response

from images.models import Image
from candidates import serializers
from candidates import models as extra_models
from elections.models import AreaType, Election
from popolo.models import Area, Membership, Person, Post
from rest_framework import pagination, viewsets

from compat import text_type

from ..election_specific import fetch_area_ids


def fetch_posts_for_area(**kwargs):
    areas = fetch_area_ids(**kwargs)

    area_ids = [area[1] for area in areas]

    posts = Post.objects.filter(
        area__identifier__in=area_ids,
    ).select_related(
        'area', 'area__extra__type', 'organization'
    ).prefetch_related(
        'extra__elections'
    )
    return posts



class UpcomingElectionsView(View):

    http_method_names = ['get']

    def get(self, request, *args, **kwargs):
        postcode = request.GET.get('postcode', None)
        coords = request.GET.get('coords', None)

        # TODO: postcode may not make sense everywhere
        errors = None
        if not postcode and not coords:
            errors = {
                'error': 'Postcode or Co-ordinates required'
            }

        try:
            posts = fetch_posts_for_area(postcode=postcode, coords=coords)
        except Exception as e:
            errors = {
                'error': e.message
            }

        if errors:
            return HttpResponse(
                json.dumps(errors), status=400, content_type='application/json'
            )

        results = []
        for post in posts.all():
            try:
                election = post.extra.elections.get(
                    current=True,
                    election_date__gte=date.today()
                )
            except:
                continue
            if election:
                results.append({
                    'post_name': post.label,
                    'post_slug': post.extra.slug,
                    'organization': post.organization.name,
                    'area': {
                        'type': post.area.extra.type.name,
                        'name': post.area.name,
                        'identifier': post.area.identifier,
                    },
                    'election_date': text_type(election.election_date),
                    'election_name': election.name,
                    'election_id': election.slug
                })

        return HttpResponse(
            json.dumps(results), content_type='application/json'
        )


class CandidatesAndElectionsForPostcodeViewSet(ViewSet):
    # This re-produces a lot of UpcomingElectionsView, but the output
    # is different enough to justify it being a different view

    http_method_names = ['get']

    def _error(self, error_msg):
        return Response({'error': error_msg}, status=400,)

    def list(self, request, *args, **kwargs):
        postcode = request.GET.get('postcode', None)
        coords = request.GET.get('coords', None)

        # TODO: postcode may not make sense everywhere
        errors = None
        if not postcode and not coords:
            return self._error('Postcode or Co-ordinates required')

        try:
            posts = fetch_posts_for_area(postcode=postcode, coords=coords)
        except Exception as e:
            return self._error(e.message)


        election_kwargs = {
            'current': True,
        }
        if 'all_elections' in request.GET:
            del election_kwargs['current']
        if 'date_gte' in request.GET:
            if request.GET['date_gte'] == "today":
                election_kwargs['election_date__gte'] = date.today()
            else:
                election_kwargs['election_date__gte'] = request.GET['date_gte']

        results = []
        for post in posts.all():
            for election in post.extra.elections.filter(**election_kwargs):
                candidates = []
                for membership in post.memberships.filter(
                        extra__election=election,
                        role=election.candidate_membership_role) \
                        .prefetch_related(
                            Prefetch(
                                'person__memberships',
                                Membership.objects.select_related(
                                    'on_behalf_of__extra',
                                    'organization__extra',
                                    'post__extra',
                                    'extra__election',
                                )
                            ),
                            Prefetch(
                                'person__extra__images',
                                Image.objects.select_related('extra__uploading_user')
                            ),
                            'person__other_names',
                            'person__contact_details',
                            'person__links',
                            'person__identifiers',
                            'person__extra_field_values',
                        ) \
                        .select_related('person__extra'):
                    candidates.append(
                        serializers.NoVersionPersonSerializer(
                            instance=membership.person,
                            context={
                                'request': request,
                            },
                            read_only=True,
                        ).data
                    )
                election = {
                    'election_date': text_type(election.election_date),
                    'election_name': election.name,
                    'election_id': election.slug,
                    'post': {
                        'post_name': post.label,
                        'post_slug': post.extra.slug,
                        'post_candidates': None
                    },
                    'area': {
                        'type': post.area.extra.type.name,
                        'name': post.area.name,
                        'identifier': post.area.identifier,
                    },
                    'organization': post.organization.name,
                    'candidates': candidates,

                }

                results.append(election)

        return Response(results)


class CurrentElectionsView(View):
    http_method_names = ['get']

    def get(self, request, *args, **kwargs):
        results = {}
        for election in Election.objects.filter(current=True).order_by('id'):
            results[election.slug] = {
                'election_date': text_type(election.election_date),
                'name': election.name,
                'url': reverse('election-detail', kwargs={
                    'version': 'v0.9',
                    'slug': election.slug})
            }

        res = HttpResponse(
            json.dumps(results), content_type='application/json',
            )
        res['Expires'] = date.today() + timedelta(days=7)
        return res


class VersionView(View):

    http_method_names = ['get']

    def get(self, request, *args, **kwargs):
        result = {
            'python_version': sys.version,
            'django_version': django.get_version(),
            'interesting_user_actions': extra_models.LoggedAction.objects \
                .exclude(action_type='set-candidate-not-elected') \
                .count(),
            'users_who_have_edited': User.objects \
                .annotate(edit_count=Count('loggedaction')) \
                .filter(edit_count__gt=0).count()
        }
        # Try to get the object name of HEAD from git:
        try:
            git_version = subprocess.check_output(
                ['git', 'rev-parse', '--verify', 'HEAD'],
                cwd=dirname(__file__),
                universal_newlines=True
            ).strip()
            result['git_version'] = git_version
        except (OSError, subprocess.CalledProcessError):
            pass
        return HttpResponse(
            json.dumps(result), content_type='application/json'
        )


class PostIDToPartySetView(View):

    http_method_names = ['get']

    def get(self, request, *args, **kwargs):
        result = dict(
            extra_models.PostExtra.objects.filter(elections__current=True) \
               .values_list('slug', 'party_set__slug')
        )
        return HttpResponse(
            json.dumps(result), content_type='application/json'
        )


# Now the django-rest-framework based API views:

class ResultsSetPagination(pagination.PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 200


class PersonViewSet(viewsets.ModelViewSet):
    queryset = Person.objects \
        .select_related('extra') \
        .prefetch_related(
            Prefetch(
                'memberships',
                Membership.objects.select_related(
                    'on_behalf_of__extra',
                    'organization__extra',
                    'post__extra',
                    'extra',
                )
            ),
            'memberships__extra__election',
            'memberships__organization__extra',
            'extra__images',
            'other_names',
            'contact_details',
            'links',
            'identifiers',
        ) \
        .order_by('id')
    serializer_class = serializers.PersonSerializer
    pagination_class = ResultsSetPagination


class OrganizationViewSet(viewsets.ModelViewSet):
    queryset = extra_models.OrganizationExtra.objects \
        .select_related('base') \
        .prefetch_related(
            'images',
            'images__extra',
            'base__contact_details',
            'base__other_names',
            'base__sources',
            'base__links',
            'base__identifiers',
            'base__parent',
            'base__parent__extra',
        ) \
        .order_by('base__id')
    lookup_field = 'slug'
    serializer_class = serializers.OrganizationExtraSerializer
    pagination_class = ResultsSetPagination


class PostViewSet(viewsets.ModelViewSet):
    queryset = extra_models.PostExtra.objects \
        .select_related(
            'base__organization__extra',
            'base__area__extra__type',
            'party_set',
        ) \
        .prefetch_related(
            'elections',
            'elections__area_types',
            'base__area__other_identifiers',
            Prefetch(
                'base__memberships',
                Membership.objects.select_related(
                    'person',
                    'on_behalf_of__extra',
                    'organization__extra',
                    'post__extra',
                    'extra__election',
                )
            )
        ) \
        .order_by('base__id')
    lookup_field = 'slug'
    serializer_class = serializers.PostExtraSerializer
    pagination_class = ResultsSetPagination


class AreaViewSet(viewsets.ModelViewSet):
    queryset = Area.objects \
        .prefetch_related('extra') \
        .order_by('id')
    serializer_class = serializers.AreaSerializer
    pagination_class = ResultsSetPagination


class AreaTypeViewSet(viewsets.ModelViewSet):
    queryset = AreaType.objects.order_by('id')
    serializer_class = serializers.AreaTypeSerializer
    pagination_class = ResultsSetPagination


class ElectionViewSet(viewsets.ModelViewSet):
    lookup_value_regex="(?!\.json$)[^/]+"
    queryset = Election.objects.order_by('id')
    lookup_field = 'slug'
    serializer_class = serializers.ElectionSerializer
    filter_fields = ('current',)
    pagination_class = ResultsSetPagination


class PartySetViewSet(viewsets.ModelViewSet):
    queryset = extra_models.PartySet.objects.order_by('id')
    serializer_class = serializers.PartySetSerializer
    pagination_class = ResultsSetPagination


class ImageViewSet(viewsets.ModelViewSet):
    queryset = Image.objects.order_by('id')
    serializer_class = serializers.ImageSerializer
    pagination_class = ResultsSetPagination


class PostExtraElectionViewSet(viewsets.ModelViewSet):
    queryset = extra_models.PostExtraElection.objects \
        .select_related('election', 'postextra') \
        .order_by('id')
    serializer_class = serializers.PostElectionSerializer
    pagination_class = ResultsSetPagination


class MembershipViewSet(viewsets.ModelViewSet):
    queryset = Membership.objects.order_by('id')
    serializer_class = serializers.MembershipSerializer
    pagination_class = ResultsSetPagination


class LoggedActionViewSet(viewsets.ModelViewSet):
    queryset = extra_models.LoggedAction.objects.order_by('id')
    serializer_class = serializers.LoggedActionSerializer
    pagination_class = ResultsSetPagination


class ExtraFieldViewSet(viewsets.ModelViewSet):
    queryset = extra_models.ExtraField.objects.order_by('id')
    serializer_class = serializers.ExtraFieldSerializer
    pagination_class = ResultsSetPagination


class SimplePopoloFieldViewSet(viewsets.ModelViewSet):
    queryset = extra_models.SimplePopoloField.objects.order_by('id')
    serializer_class = serializers.SimplePopoloFieldSerializer
    pagination_class = ResultsSetPagination


class ComplexPopoloFieldViewSet(viewsets.ModelViewSet):
    queryset = extra_models.ComplexPopoloField.objects.order_by('id')
    serializer_class = serializers.ComplexPopoloFieldSerializer
    pagination_class = ResultsSetPagination
