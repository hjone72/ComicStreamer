#!/usr/bin/env python
import sys
import os
import hashlib
import md5
import mmap
import datetime
import time
import threading
import Queue
import logging
from watchdog.observers import Observer
from watchdog.events import LoggingEventHandler
import watchdog

from comicapi.comicarchive import *
from comicapi.issuestring import *
import utils

from database import *
from library import Library

class  MonitorEventHandler(watchdog.events.FileSystemEventHandler):
    
    def __init__(self, monitor):
        self.monitor = monitor
        self.ignore_directories = True
        
    def on_any_event(self,event):
        if event.is_directory:
            return
        self.monitor.handleSingleEvent(event)


class Monitor():
        
    def __init__(self, dm, paths):
        
        self.dm = dm
        self.style = MetaDataStyle.CIX
        self.queue = Queue.Queue(0)
        self.paths = paths
        self.eventList = []
        self.mutex = threading.Lock()
        self.eventProcessingTimer = None
        self.quit_when_done = False  # for debugging/testing
        self.status = "IDLE"
        self.statusdetail = ""
        self.scancomplete_ts = ""

    def start(self):
        self.thread = threading.Thread(target=self.mainLoop)
        self.thread.daemon = True
        self.quit = False
        self.thread.start()     

    def stop(self):
        self.quit = True
        self.thread.join()

    def mainLoop(self):

        logging.debug("Monitor: started main loop.")
        self.session = self.dm.Session()
        self.library = Library(self.dm.Session)
        
        observer = Observer()
        self.eventHandler = MonitorEventHandler(self)
        for path in self.paths:
            if os.path.exists(path):
                observer.schedule(self.eventHandler, path, recursive=True)
        observer.start()
        
        while True:
            try:
                (msg, args) = self.queue.get(block=True, timeout=1)
            except:
                msg = None
                
            #dispatch messages
            if msg == "scan":
                self.dofullScan(self.paths)

            if msg == "events":
                self.doEventProcessing(args)
            
            #time.sleep(1)
            if self.quit:
                break
            
        self.session.close()
        self.session = None
        observer.stop()
        logging.debug("Monitor: stopped main loop.")
        
    def scan(self):
        self.queue.put(("scan", None))
    
    def handleSingleEvent(self, event):
        # events may happen in clumps.  start a timer
        # to defer processing.  if the timer is already going,
        # it will be canceled
        
        # in the future there can be more smarts about
        # granular file events.  for now this will be
        # good enough to just get a a trigger that *something*
        # changed
        
        self.mutex.acquire()
        
        if self.eventProcessingTimer is not None:
            self.eventProcessingTimer.cancel()
        self.eventProcessingTimer = threading.Timer(30, self.handleEventProcessing)
        self.eventProcessingTimer.start()
        
        self.mutex.release()
        

    
    def handleEventProcessing(self):
        
        # trigger a full rescan
        self.mutex.acquire()
        
        self.scan()
        
        # remove the timer
        if self.eventProcessingTimer is not None:
            self.eventProcessingTimer = None
            
        self.mutex.release()


    def checkIfRemovedOrModified(self, comic, pathlist):
        remove = False
        logging.info(u"Made it to checking")
        def inFolderlist(filepath, pathlist):
            for p in pathlist:
                if p in filepath:
                    return True
            return False
        
        if not (os.path.exists(comic.path)):
            # file is missing, remove it from the comic table, add it to deleted table
            logging.debug(u"Removing missing {0}".format(comic.path))
            logging.debug(u"As it turns out I'm not actually going to delete that file... It will probably reappear...")
            #remove = True
        elif not inFolderlist(comic.path, pathlist):
            logging.debug(u"Removing unwanted {0}".format(comic.path))
            logging.debug(u"This will never happen and must be an error. Not deleting anything.")
            #remove = True
        else:
            # file exists.  check the mod date.
            # if it's been modified, remove it, and it'll be re-added
            #curr = datetime.datetime.fromtimestamp(os.path.getmtime(comic.path))
            curr = datetime.utcfromtimestamp(os.path.getmtime(comic.path))
            prev = comic.mod_ts
            if curr != prev:
                logging.debug(u"Removed modifed {0}".format(comic.path))
                logging.debug(u"File has been modified but i'll trust that it hasn't changed.")
                #remove = True
           
        return remove

    def getComicMetadata(self, path):
	print("Getting metadata: " + path)
        ca = ComicArchive(path,  default_image_path=AppFolders.imagePath("default.jpg"))
        
        if ca.seemsToBeAComicArchive():
            logging.debug(u"Reading in {0} {1}\r".format(self.read_count, path))
            sys.stdout.flush()
            self.read_count += 1

            if ca.hasMetadata( MetaDataStyle.CIX ):
                style = MetaDataStyle.CIX
            elif ca.hasMetadata( MetaDataStyle.CBI ):
                style = MetaDataStyle.CBI
            else:
                style = None
                
            if style is not None:
                md = ca.readMetadata(style)
            else:
                # No metadata in comic.  make some guesses from the filename
                md = ca.metadataFromFilename()
                
            md.path = ca.path 
            md.page_count = ca.page_count
            md.mod_ts = datetime.utcfromtimestamp(time.time())
            md.filesize = md.page_count*100
            #os.path.getsize(md.path)
            md.hash = ""

            #thumbnail generation
            image_data = ca.getPage(0)
            #now resize it
            thumb = StringIO.StringIO()
            utils.resize(image_data, (200, 200), thumb)
            md.thumbnail = thumb.getvalue()

            return md
        return None

    def setStatusDetail(self, detail, level=logging.DEBUG):
        self.statusdetail = detail
        if level == logging.DEBUG:
            logging.debug(detail)
        else:
            logging.info(detail)

    def setStatusDetailOnly(self, detail):
        self.statusdetail = detail
            
    def commitMetadataList(self, md_list):
        comics = []
        for md in md_list:
            self.add_count += 1
            comic = self.library.createComicFromMetadata(md)
            comics.append(comic)
            if self.quit:
                self.setStatusDetail(u"Monitor: halting scan!")
                return

        self.library.addComics(comics)

    def createAddRemoveLists(self, dirs):
        ix = {}
        db_set = set()
        current_set = set()
        filelist = utils.get_recursive_filelist(dirs)
        ts = time.time()
        for path in filelist:
            #current_set.add((path, datetime.utcfromtimestamp(os.path.getmtime(path))))
            #getmtime from acd is intensive! and I don't care about Mtime. Disable this.
            current_set.add((path, datetime.utcfromtimestamp(ts)))
        logging.info("NEW -- current_set size [%d]" % len(current_set))

        for comic_id, path, md_ts in self.library.getComicPaths():
            db_set.add((path, datetime.utcfromtimestamp(ts)))
            ix[path] = comic_id
        to_add = current_set - db_set
        to_remove = db_set - current_set
        logging.info("NEW -- db_set size [%d]" % len(db_set))
        logging.info("NEW -- to_add size [%d]" % len(to_add))
        logging.info("NEW -- to_remove size [%d]" % len(to_remove))

        return [r[0] for r in to_add], [ix[r[0]] for r in to_remove]

    def dofullScan(self, dirs):
        
        self.status = "SCANNING"
        
        logging.info(u"Monitor: Beginning file scan...")
        self.setStatusDetail(u"Monitor: Making a list of all files in the folders...")

        self.add_count = 0      
        self.remove_count = 0


        filelist, to_remove = self.createAddRemoveLists(dirs)

        self.setStatusDetail(u"Monitor: Removing missing or modified files from db ({0} files)".format(len(to_remove)), logging.INFO)
        if len(to_remove) > 0:
            #self.library.deleteComics(to_remove)
            logging.debug(u"Not going to remove anything")

        self.setStatusDetail(u"Monitor: {0} new files to scan...".format(len(filelist)), logging.INFO)

        md_list = []
        self.read_count = 0
        for filename in filelist:
            md = self.getComicMetadata(filename)
            if md is not None:
                md_list.append(md)
            self.setStatusDetailOnly(u"Monitor: {0} files: {1} scanned, {2} added to library...".format(len(filelist), self.read_count,self.add_count))
            if self.quit:
                self.setStatusDetail(u"Monitor: halting scan!")
                return
            
            #every so often, commit to DB
            if self.read_count % 10 == 0 and self.read_count != 0:
                if len(md_list) > 0:
                    self.commitMetadataList(md_list)
                    md_list = []
        
        if len(md_list) > 0:
            self.commitMetadataList(md_list)
        
        self.setStatusDetail(u"Monitor: finished scanning metadata in {0} of {1} files".format(self.read_count,len(filelist)), logging.INFO)

 
        
        self.status = "IDLE"
        self.statusdetail = ""
        self.scancomplete_ts = int(time.mktime(datetime.utcnow().timetuple()) * 1000)
        
        logging.info("Monitor: Added {0} comics".format(self.add_count))
        logging.info("Monitor: Removed {0} comics".format(self.remove_count))
            
        if self.quit_when_done:
            self.quit = True

    def doEventProcessing(self, eventList):
        logging.debug(u"Monitor: event_list:{0}".format(eventList))

        
if __name__ == '__main__':
    
    if len(sys.argv) < 2:
        print >> sys.stderr, "usage:  {0} comic_folder ".format(sys.argv[0])
        sys.exit(-1)    

    
    utils.fix_output_encoding()
    
    dm = DataManager()
    dm.create()
    m = Monitor(dm, sys.argv[1:])
    m.quit_when_done = True
    m.start()
    m.scan()

    #while True:
    #   time.sleep(10)

    m.stop()
