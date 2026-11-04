"""Conservative Jellyfin rating rules independent of any live family library."""
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import jellyfin_age_policy as age

class AgePolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy={'mode':'automatic','system':'us','maximum':'PG','seriesFallback':False}
        self.movie={'Id':'movie','Type':'Movie','ProviderIds':{'Tmdb':'123'},'OfficialRating':'PG'}
        self.series={'Id':'show','Type':'Series','ProviderIds':{'Tvdb':'123'},'OfficialRating':'PG'}
        self.episode={'Id':'ep','Type':'Episode','SeriesId':'show','OfficialRating':'PG'}
    def test_fail_closed_for_unknown_identity_or_rating(self):
        self.assertTrue(age.evaluate(self.movie,self.policy)['eligible'])
        for changed in ({'ProviderIds':{}},{'ProviderIds':{'Tmdb':'not-valid'}},{'OfficialRating':None},{'OfficialRating':'TV-G'},{'Type':'Video'},{'OfficialRating':'PG-13'},{'OfficialRating':'GB:PG'}):
            self.assertFalse(age.evaluate({**self.movie,**changed},self.policy)['eligible'],changed)
    def test_series_must_match_and_fallback_is_explicit(self):
        self.assertTrue(age.evaluate(self.episode,self.policy,self.series)['eligible'])
        self.assertFalse(age.evaluate(self.episode,self.policy,{**self.series,'Id':'other'})['eligible'])
        missing={**self.episode,'OfficialRating':None}
        self.assertFalse(age.evaluate(missing,self.policy,self.series)['eligible'])
        self.assertTrue(age.evaluate(missing,{**self.policy,'seriesFallback':True},self.series)['eligible'])
        self.assertFalse(age.evaluate(missing,{**self.policy,'seriesFallback':True},{**self.series,'ProviderIds':{}})['eligible'])
    def test_uk_12_and_12a_are_equivalent(self):
        self.assertTrue(age.evaluate({**self.movie,'OfficialRating':'12A'},{**self.policy,'system':'gb','maximum':'12'})['eligible'])
    def test_default_manual_and_validation(self):
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE profiles (id TEXT PRIMARY KEY)');db.execute("INSERT INTO profiles VALUES ('family')");age.init(db)
        self.assertEqual(age.load(db,'family')['mode'],'manual')
        with self.assertRaises(ValueError):age.save(db,'family',{**self.policy,'maximum':'unrated'})
        age.save(db,'family',self.policy);self.assertEqual(age.load(db,'family'),self.policy)

if __name__=='__main__':unittest.main()
