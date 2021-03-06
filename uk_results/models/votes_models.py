from __future__ import unicode_literals

from django.db import models
from django.db import transaction

from .base import BaseResultModel, ResultStatusMixin


class PostResultManager(models.Manager):
    def confirmed(self):
        return self.filter(confirmed=True)


class PostResult(models.Model):
    post = models.ForeignKey('popolo.Post')
    confirmed = models.BooleanField(default=False)
    confirmed_resultset = models.OneToOneField(
        'ResultSet', null=True)

    objects = PostResultManager()

    @models.permalink
    def get_absolute_url(self):
        return ('post-results-view', (), {'post_id': self.post.extra.slug})


class ResultSet(BaseResultModel, ResultStatusMixin):
    post_result = models.ForeignKey(
        PostResult,
        related_name='result_sets',
    )

    # num_turnout_calculated = models.IntegerField()
    num_turnout_reported = models.IntegerField(null=True)
    num_spoilt_ballots = models.IntegerField(null=True)

    user = models.ForeignKey(
        'auth.User',
        related_name='result_sets',
        null=True,
    )

    ip_address = models.GenericIPAddressField()

    def __str__(self):
        return u"pk=%d user=%r post=%r" % (
            self.pk,
            self.user,
            self.post_result,
        )

    def save(self, *args, **kwargs):
        super(ResultSet, self).save(*args, **kwargs)
        if self.review_status == "confirmed":
            self.post_result.confirmed = True
            self.post_result.confirmed_resultset = self
            if not self.review_source:
                self.review_source = self.source
            self.post_result.save()



class CandidateResult(BaseResultModel):
    result_set = models.ForeignKey(
        'ResultSet',
        related_name='candidate_results',
    )

    membership = models.ForeignKey(
        'popolo.Membership',
        related_name='result',
    )

    num_ballots_reported = models.IntegerField()
    is_winner = models.BooleanField(default=False)


    class Meta:
        ordering = ('-num_ballots_reported',)
        unique_together = (
            ('result_set', 'membership'),
        )

    def __unicode__(self):
        return u"{} ({} votes)".format(
            self.membership.person,
            self.num_ballots_reported
        )
