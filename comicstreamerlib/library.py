"""Encapsulates all data acces code to maintain the comic library"""
from datetime import datetime
import dateutil
import os

from sqlalchemy import func, distinct
from sqlalchemy.orm import subqueryload

import utils
from database import Comic, DatabaseInfo, Person, Role, Credit, Character, GenericTag, Team, Location, \
    StoryArc, Genre, DeletedComic, Users
from folders import AppFolders
from comicapi.comicarchive import ComicArchive
from comicapi.issuestring import IssueString

import logging
import logging.handlers

class Library:

    def __init__(self, session_getter):
        self.getSession = session_getter
        self.comicArchiveList = []
        self.namedEntities = {}

    def getSession(self):
        """SQLAlchemy session"""
        pass

    def getComicThumbnail(self, comic_id):
        """Fast access to a comic thumbnail"""
        return self.getSession().query(Comic.thumbnail) \
                   .filter(Comic.id == int(comic_id)).scalar()

    def getComic(self, comic_id):
        return self.getSession().query(Comic).get(int(comic_id))

    def getUserInfo(self, comic_id, user):
        ui = self.getSession().query(Users).filter(Users.id == int(user)).filter(Users.comic_id == int(comic_id)).first()
        if ui is None:
            #Build user/comic record.
            record = Users()
            record.id = user
            record.comic_id = comic_id
            self.getSession().add(record)
        logging.debug(u"User Info: {0}".format(ui))
        return ui

    def getComicPage(self, comic_id, page_number, max_height = None):
        (path, page_count) = self.getSession().query(Comic.path, Comic.page_count) \
                                 .filter(Comic.id == int(comic_id)).first()

        image_data = None
        default_img_file = AppFolders.imagePath("default.jpg")

        if path is not None:
            if int(page_number) < page_count:
                ca = self.getComicArchive(path)
                image_data = ca.getPage(int(page_number))

        if image_data is None:
            with open(default_img_file, 'rb') as fd:
                image_data = fd.read()
            return image_data

        # resize image
        if max_height is not None:
            try:
                image_data = utils.resizeImage(int(max_height), image_data)
            except Exception as e:
                #logging.error(e)
                pass
        return image_data

    def getStats(self):
        stats = {}
        session = self.getSession()
        stats['total'] = session.query(Comic).count()

        dbinfo = session.query(DatabaseInfo).first()
        stats['uuid'] = dbinfo.uuid
        stats['last_updated'] = dbinfo.last_updated
        stats['created'] = dbinfo.created

        stats['series'] = session.query(func.count(distinct(Comic.series))).scalar()
        stats['persons'] = session.query(Person).count()

        return stats

    def getComicPaths(self):
        return self.getSession().query(Comic.id, Comic.path, Comic.mod_ts).all()

    def recentlyAddedComics(self, limit = 10):
        return self.getSession().query(Comic)\
                   .order_by(Comic.added_ts.desc())\
                   .limit(limit)

    def recentlyReadComics(self, limit = 10, user = 1):
        myRecentlyReadComics = self.getSession().query(Users).filter(Users.id == user).filter(Users.lastread_ts != "").order_by(Users.lastread_ts.desc()).limit(limit)
        Comics = []
        for comic in myRecentlyReadComics:
            readComic = self.getSession().query(Comic).filter(Comic.id == int(comic.comic_id)).first()
            if readComic is not None:
                Comics.append(self.getSession().query(Comic).filter(Comic.id == int(comic.comic_id)).first())
        return Comics

    def getRoles(self):
        return self.getSession().query(Role).all()

    def randomComic(self):
        # SQLite specific random call
        return self.getSession().query(Comic)\
                   .order_by(func.random()).limit(1).first()

    def getDeletedComics(self, since=None):
        # get all deleted comics first
        session = self.getSession()
        resultset = session.query(DeletedComic)

        # now winnow it down with timestampe, if requested
        if since is not None:
            try:
                dt = dateutil.parser.parse(since)
                resultset = resultset.filter(DeletedComic.ts >= dt)
            except:
                pass
        return resultset.all()

    def createComicFromMetadata(self, md):

        comic = Comic()
        # store full path, and filename and folder separately, for search efficiency,
        # at the cost of redundant storage
        comic.folder, comic.file = os.path.split(md.path)
        comic.path = md.path

        comic.page_count = md.page_count
        comic.mod_ts = md.mod_ts
        comic.hash = md.hash
        comic.filesize = md.filesize
        comic.thumbnail = md.thumbnail

        if not md.isEmpty:
            if md.series is not None:
                comic.series = unicode(md.series)
            if md.issue is not None:
                comic.issue = unicode(md.issue)
                comic.issue_num = IssueString(unicode(comic.issue)).asFloat()

            if md.year is not None:
                try:
                    day = 1
                    month = 1
                    if md.month is not None:
                        month = int(md.month)
                    if md.day is not None:
                        day = int(md.day)
                    year = int(md.year)
                    comic.date = datetime(year, month, day)
                except:
                    pass

            comic.year = md.year
            comic.month = md.month
            comic.day = md.day

            if md.volume is not None:
                comic.volume = int(md.volume)
            if md.publisher is not None:
                comic.publisher = unicode(md.publisher)
            if md.title is not None:
                comic.title = unicode(md.title)
            if md.comments is not None:
                comic.comments = unicode(md.comments)
            if md.imprint is not None:
                comic.imprint = unicode(md.imprint)
            if md.webLink is not None:
                comic.weblink = unicode(md.webLink)

        if md.characters is not None:
            for c in list(set(md.characters.split(","))):
                character = self.getNamedEntity(Character, c.strip())
                comic.characters_raw.append(character)

        if md.teams is not None:
            for t in list(set(md.teams.split(","))):
                team = self.getNamedEntity(Team, t.strip())
                comic.teams_raw.append(team)

        if md.locations is not None:
            for l in list(set(md.locations.split(","))):
                location = self.getNamedEntity(Location, l.strip())
                comic.locations_raw.append(location)

        if md.storyArc is not None:
            for sa in list(set(md.storyArc.split(","))):
                storyarc = self.getNamedEntity(StoryArc, sa.strip())
                comic.storyarcs_raw.append(storyarc)

        if md.genre is not None:
            for g in list(set(md.genre.split(","))):
                genre = self.getNamedEntity(Genre,  g.strip())
                comic.genres_raw.append(genre)

        if md.tags is not None:
            for gt in list(set(md.tags)):
                generictag = self.getNamedEntity(GenericTag,  gt.strip())
                comic.generictags_raw.append(generictag)

        if md.credits is not None:
            for credit in md.credits:
                role = self.getNamedEntity(Role, credit['role'].lower().strip())
                person = self.getNamedEntity(Person, credit['person'].strip())
                comic.credits_raw.append(Credit(person, role))

        return comic

    def getNamedEntity(self, cls, name):
        """Gets related entities such as Characters, Persons etc by name"""
        # this is a bit hackish, we need unique instances between successive
        # calls for newly created entities, to avoid unique violations at flush time
        key = (cls, name)
        if key not in self.namedEntities.keys():
            obj = self.getSession().query(cls).filter(cls.name == name).first()
            self.namedEntities[key] = obj
            if obj is None:
                self.namedEntities[key] = cls(name=name)
        return self.namedEntities[key]

    def addComics(self, comic_list):
        for comic in comic_list:
            self.getSession().add(comic)
        if len(comic_list) > 0:
            self._dbUpdated()
        self.getSession().commit()

    def deleteComics(self, comic_id_list):
        s = self.getSession()
        for comic_id in comic_id_list:
            deleted = DeletedComic()
            deleted.comic_id = int(comic_id)
            s.add(deleted)
            s.delete(s.query(Comic).get(comic_id))
        if len(comic_id_list) > 0:
            self._dbUpdated()
        s.commit()

    def _dbUpdated(self):
        """Updates DatabaseInfo status"""
        self.getSession().query(DatabaseInfo).first().last_updated = datetime.utcnow()

    def list(self, criteria={}, paging=None):
        if paging is None:
            paging = {'per_page': 10, 'offset': 1}

        query = self.getSession().query(Comic)

        query = self.processComicQueryArgs(query, criteria)
        query, total_results = self.processPagingArgs(query, paging)
        query = query.options(subqueryload('characters_raw'))
        query = query.options(subqueryload('storyarcs_raw'))
        query = query.options(subqueryload('locations_raw'))
        query = query.options(subqueryload('teams_raw'))
        #query = query.options(subqueryload('credits_raw'))
        query = query.options(subqueryload('generictags_raw'))

        return query.all(), total_results

    def processPagingArgs(self, query, paging):
        per_page = paging.get(u"per_page", None)
        offset = paging.get(u"offset", None)
        # offset and max_results should be processed last

        total_results = None
        if per_page is not None:
            total_results = query.distinct().count()
            try:
                max = 0
                max = int(per_page)
                if total_results > max:
                    query = query.limit(max)
            except:
                pass

        if offset is not None:
            try:
                off = 0
                off = int(offset)
                query = query.offset(off)
            except:
                pass

        return query, total_results

    def processComicQueryArgs(self, query, criteria):
        def hasValue(obj):
            return obj is not None and obj != ""

        keyphrase_filter = criteria.get(u"keyphrase", None)
        series_filter = criteria.get(u"series", None)
        path_filter = criteria.get(u"path", None)
        folder_filter = criteria.get(u"folder", "")
        title_filter = criteria.get(u"title", None)
        start_filter = criteria.get(u"start_date", None)
        end_filter = criteria.get(u"end_date", None)
        added_since = criteria.get(u"added_since", None)
        modified_since = criteria.get(u"modified_since", None)
        lastread_since = criteria.get(u"lastread_since", None)
        order = criteria.get(u"order", None)
        character = criteria.get(u"character", None)
        team = criteria.get(u"team", None)
        location = criteria.get(u"location", None)
        storyarc = criteria.get(u"storyarc", None)
        volume = criteria.get(u"volume", None)
        publisher = criteria.get(u"publisher", None)
        credit_filter = criteria.get(u"credit", None)
        tag = criteria.get(u"tag", None)
        genre = criteria.get(u"genre", None)

        if folder_filter is not None and folder_filter != "":
            folder_filter = os.path.normcase(os.path.normpath(folder_filter))

        person = None
        role = None
        if hasValue(credit_filter):
            credit_info = credit_filter.split(":")
            if len(credit_info[0]) != 0:
                person = credit_info[0]
                if len(credit_info) > 1:
                    role = credit_info[1]

        if hasValue(person):
            query = query.join(Credit)\
                         .filter(Person.name.ilike(person.replace("*", "%"))) \
                         .filter(Credit.person_id == Person.id)
            if role is not None:
                query = query.filter(Credit.role_id == Role.id) \
                             .filter(Role.name.ilike(role.replace("*", "%")))
            #query = query.filter( Comic.persons.contains(unicode(person).replace("*","%") ))

        if hasValue(keyphrase_filter):
            keyphrase_filter = unicode(keyphrase_filter).replace("*", "%")
            keyphrase_filter = "%" + keyphrase_filter + "%"
            query = query.filter( Comic.series.ilike(keyphrase_filter)
                                | Comic.title.ilike(keyphrase_filter)
                                | Comic.publisher.ilike(keyphrase_filter)
                                | Comic.path.ilike(keyphrase_filter)
                                | Comic.comments.ilike(keyphrase_filter)
                                #| Comic.characters_raw.any(Character.name.ilike(keyphrase_filter))
                                #| Comic.teams_raw.any(Team.name.ilike(keyphrase_filter))
                                #| Comic.locations_raw.any(Location.name.ilike(keyphrase_filter))
                                #| Comic.storyarcs_raw.any(StoryArc.name.ilike(keyphrase_filter))
                                | Comic.persons_raw.any(Person.name.ilike(keyphrase_filter))
                            )

        def addQueryOnScalar(query, obj_prop, filt):
            if hasValue(filt):
                filt = unicode(filt).replace("*","%")
                return query.filter( obj_prop.ilike(filt))
            else:
                return query

        def addQueryOnList(query, obj_list, list_prop, filt):
            if hasValue(filt):
                filt = unicode(filt).replace("*","%")
                return query.filter( obj_list.any(list_prop.ilike(filt)))
            else:
                return query

        query = addQueryOnScalar(query, Comic.series, series_filter)
        query = addQueryOnScalar(query, Comic.title, title_filter)
        query = addQueryOnScalar(query, Comic.path, path_filter)
        query = addQueryOnScalar(query, Comic.folder, folder_filter)
        query = addQueryOnScalar(query, Comic.publisher, publisher)
        query = addQueryOnList(query, Comic.characters_raw, Character.name, character)
        query = addQueryOnList(query, Comic.generictags_raw, GenericTag.name, tag)
        query = addQueryOnList(query, Comic.teams_raw, Team.name, team)
        query = addQueryOnList(query, Comic.locations_raw, Location.name, location)
        query = addQueryOnList(query, Comic.storyarcs_raw, StoryArc.name, storyarc)
        query = addQueryOnList(query, Comic.genres_raw, Genre.name, genre)

        if hasValue(volume):
            try:
                vol = 0
                vol = int(volume)
                query = query.filter(Comic.volume == vol)
            except:
                pass

        if hasValue(start_filter):
            try:
                dt = dateutil.parser.parse(start_filter)
                query = query.filter(Comic.date >= dt)
            except:
                pass

        if hasValue(end_filter):
            try:
                dt = dateutil.parser.parse(end_filter)
                query = query.filter(Comic.date <= dt)
            except:
                pass

        if hasValue(modified_since):
            try:
                dt = dateutil.parser.parse(modified_since)
                query = query.filter(Comic.mod_ts >= dt)
            except:
                pass

        if hasValue(added_since):
            try:
                dt = dateutil.parser.parse(added_since)
                query = query.filter(Comic.added_ts >= dt)
            except:
                pass

        if hasValue(lastread_since):
            try:
                dt = dateutil.parser.parse(lastread_since)
                query = query.filter(Comic.lastread_ts >= dt, Comic.lastread_ts != "")
            except:
                pass

        order_key = None
        # ATB temp hack to cover "slicing" bug where
        # if no order specified, the child collections
        # get chopped off sometimes
        if not hasValue(order):
            order = "id"

        if hasValue(order):
            if order[0] == "-":
                order_desc = True
                order = order[1:]
            else:
                order_desc = False
            if order == "id":
                order_key = Comic.id
            if order == "series":
                order_key = Comic.series
            elif order == "modified":
                order_key = Comic.mod_ts
            elif order == "added":
                order_key = Comic.added_ts
            elif order == "lastread":
                order_key = Comic.lastread_ts
            elif order == "volume":
                order_key = Comic.volume
            elif order == "issue":
                order_key = Comic.issue_num
            elif order == "date":
                order_key = Comic.date
            elif order == "publisher":
                order_key = Comic.publisher
            elif order == "title":
                order_key = Comic.title
            elif order == "path":
                order_key = Comic.path

        if order_key is not None:
            if order_desc:
                order_key = order_key.desc()
            query = query.order_by(order_key)

        return query

    def getComicArchive(self, path):
        # should also look at modified time of file
        for ca in self.comicArchiveList:
            if ca.path == path:
                # remove from list and put at end
                self.comicArchiveList.remove(ca)
                self.comicArchiveList.append(ca)
                return ca
        else:
            ca = ComicArchive(path, default_image_path=AppFolders.imagePath("default.jpg"))
            self.comicArchiveList.append(ca)
            if len(self.comicArchiveList) > 10:
                self.comicArchiveList.pop(0)
            return ca
