#Embedded file name: ACEStream\Core\BuddyCast\bartercast.pyo
import sys, os
from ACEStream.Core.BitTornado.bencode import bencode, bdecode
from ACEStream.Core.Statistics.Logger import OverlayLogger
from ACEStream.Core.BitTornado.BT1.MessageID import BARTERCAST
from ACEStream.Core.CacheDB.CacheDBHandler import BarterCastDBHandler
from ACEStream.Core.Utilities.utilities import *
from traceback import print_exc
from types import StringType, ListType, DictType
from time import time, gmtime, strftime, ctime
from ACEStream.Core.Overlay.permid import permid_for_user
from ACEStream.Core.Overlay.SecureOverlay import OLPROTO_VER_FIFTH
MAX_BARTERCAST_LENGTH = 10485760
NO_PEERS_IN_MSG = 10
REFRESH_TOPN_INTERVAL = 1800
DEBUG = False
LOG = False

def now():
    return int(time())


class BarterCastCore:

    def __init__(self, data_handler, overlay_bridge, log = '', dnsindb = None):
        if DEBUG:
            print >> sys.stderr, '=================Initializing bartercast core'
        self.data_handler = data_handler
        self.dnsindb = dnsindb
        self.log = log
        self.overlay_bridge = overlay_bridge
        self.bartercastdb = BarterCastDBHandler.getInstance()
        self.network_delay = 30
        self.send_block_list = {}
        self.recv_block_list = {}
        self.block_interval = 3600
        self.topn = self.bartercastdb.getTopNPeers(NO_PEERS_IN_MSG, local_only=True)['top']
        self.overlay_bridge.add_task(self.refreshTopN, REFRESH_TOPN_INTERVAL)
        if self.log:
            self.overlay_log = OverlayLogger.getInstance(self.log)
        if LOG:
            self.logfile = '/Users/michel/packages/bartercast_dataset/bartercast42.log'
            if not os.path.exists(self.logfile):
                log = open(self.logfile, 'w')
                log.close()

    def refreshTopN(self):
        self.topn = self.bartercastdb.getTopNPeers(NO_PEERS_IN_MSG, local_only=True)['top']
        self.overlay_bridge.add_task(self.refreshTopN, REFRESH_TOPN_INTERVAL)

    def createAndSendBarterCastMessage(self, target_permid, selversion, active = False):
        if selversion <= OLPROTO_VER_FIFTH:
            return
        if DEBUG:
            print >> sys.stderr, '===========bartercast: Sending BarterCast msg to ', self.bartercastdb.getName(target_permid)
        bartercast_data = self.createBarterCastMessage(target_permid)
        if LOG:
            self.logMsg(bartercast_data, target_permid, 'out', logfile=self.logfile)
        try:
            bartercast_msg = bencode(bartercast_data)
        except:
            print_exc()
            print >> sys.stderr, 'error bartercast_data:', bartercast_data
            return

        self.overlay_bridge.send(target_permid, BARTERCAST + bartercast_msg, self.bartercastSendCallback)
        self.blockPeer(target_permid, self.send_block_list, self.block_interval)

    def createBarterCastMessage(self, target_permid):
        my_permid = self.bartercastdb.my_permid
        local_top = self.topn
        top_peers = map(lambda (permid, up, down): permid, local_top)
        data = {}
        totals = self.bartercastdb.getTotals()
        for permid in top_peers:
            item = self.bartercastdb.getItem((my_permid, permid))
            if item is not None:
                data_to = item['uploaded']
                data_from = item['downloaded']
                data[permid] = {'u': data_to,
                 'd': data_from}

        bartercast_data = {'data': data,
         'totals': totals}
        return bartercast_data

    def bartercastSendCallback(self, exc, target_permid, other = 0):
        if exc is None:
            if DEBUG:
                print 'bartercast: %s *** msg was sent successfully to peer %s' % (ctime(now()), self.bartercastdb.getName(target_permid))
        elif DEBUG:
            print 'bartercast: %s *** warning - error in sending msg to %s' % (ctime(now()), self.bartercastdb.getName(target_permid))

    def gotBarterCastMessage(self, recv_msg, sender_permid, selversion):
        if DEBUG:
            print >> sys.stderr, 'bartercast: %s Received a BarterCast msg from %s' % (ctime(now()), self.bartercastdb.getName(sender_permid))
        if not sender_permid or sender_permid == self.bartercastdb.my_permid:
            print >> sys.stderr, 'bartercast: error - got BarterCastMsg from a None peer', sender_permid, recv_msg
            return False
        if MAX_BARTERCAST_LENGTH > 0 and len(recv_msg) > MAX_BARTERCAST_LENGTH:
            print >> sys.stderr, 'bartercast: warning - got large BarterCastMsg', len(recv_msg)
            return False
        bartercast_data = {}
        try:
            bartercast_data = bdecode(recv_msg)
        except:
            print >> sys.stderr, 'bartercast: warning, invalid bencoded data'
            return False

        try:
            self.validBarterCastMsg(bartercast_data)
        except RuntimeError as msg:
            print >> sys.stderr, msg
            return False

        if LOG:
            self.logMsg(bartercast_data, sender_permid, 'in', logfile=self.logfile)
        data = bartercast_data['data']
        if 'totals' in bartercast_data:
            totals = bartercast_data['totals']
        else:
            totals = None
        if DEBUG:
            st = time()
            self.handleBarterCastMsg(sender_permid, data)
            et = time()
            diff = et - st
            print >> sys.stderr, 'bartercast: HANDLE took %.4f' % diff
        else:
            self.handleBarterCastMsg(sender_permid, data, totals)
        if not self.isBlocked(sender_permid, self.send_block_list):
            self.replyBarterCast(sender_permid, selversion)
        return True

    def validBarterCastMsg(self, bartercast_data):
        if not type(bartercast_data) == DictType:
            raise RuntimeError, 'bartercast: received data is not a dictionary'
            return False
        if not bartercast_data.has_key('data'):
            raise RuntimeError, "bartercast: 'data' key doesn't exist"
            return False
        if not type(bartercast_data['data']) == DictType:
            raise RuntimeError, "bartercast: 'data' value is not dictionary"
            return False
        for permid in bartercast_data['data'].keys():
            if not bartercast_data['data'][permid].has_key('u') or not bartercast_data['data'][permid].has_key('d'):
                raise RuntimeError, "bartercast: datafield doesn't contain 'u' or 'd' keys"
                return False

        return True

    def handleBarterCastMsg(self, sender_permid, data, totals = None):
        if DEBUG:
            print >> sys.stderr, 'bartercast: Processing bartercast msg from: ', self.bartercastdb.getName(sender_permid)
            print >> sys.stderr, 'totals: ', totals
        permids = data.keys()
        changed = False
        self.bartercastdb.addPeersBatch(permids)
        if totals != None and len(totals) == 2:
            up = int(totals[0])
            down = int(totals[1])
            self.bartercastdb.updateULDL((sender_permid, sender_permid), up, down, commit=False)
            changed = True
        datalen = len(permids)
        for i in range(0, datalen):
            permid = permids[i]
            data_to = data[permid]['u']
            data_from = data[permid]['d']
            if DEBUG:
                print >> sys.stderr, 'bartercast: data: (%s, %s) up = %d down = %d' % (self.bartercastdb.getName(sender_permid),
                 self.bartercastdb.getName(permid),
                 data_to,
                 data_from)
            self.bartercastdb.updateULDL((sender_permid, permid), data_to, data_from, commit=False)
            changed = True

        if changed:
            self.bartercastdb.commit()

    def replyBarterCast(self, target_permid, selversion):
        if DEBUG:
            st = time()
            self.createAndSendBarterCastMessage(target_permid, selversion)
            et = time()
            diff = et - st
            print >> sys.stderr, 'bartercast: CREATE took %.4f' % diff
        else:
            self.createAndSendBarterCastMessage(target_permid, selversion)

    def isBlocked(self, peer_permid, block_list):
        if peer_permid not in block_list:
            return False
        unblock_time = block_list[peer_permid]
        if now() >= unblock_time - self.network_delay:
            block_list.pop(peer_permid)
            return False
        return True

    def blockPeer(self, peer_permid, block_list, block_interval = None):
        if block_interval is None:
            block_interval = self.block_interval
        unblock_time = now() + block_interval
        block_list[peer_permid] = unblock_time
        if DEBUG:
            print >> sys.stderr, 'bartercast: %s Blocked peer %s' % (ctime(now()), self.bartercastdb.getName(peer_permid))

    def logMsg(self, msg_data, msg_permid, in_or_out, logfile):
        if in_or_out == 'in':
            permid_from = permid_for_user(msg_permid)
        elif in_or_out == 'out':
            permid_from = 'LOCAL'
        else:
            return
        timestamp = now()
        log = open(logfile, 'a')
        string = '%.1f %s %s' % (timestamp, in_or_out, permid_for_user(msg_permid))
        log.write(string + '\n')
        print >> sys.stderr, string
        data = msg_data.get('data', [])
        for permid in data:
            u = data[permid]['u']
            d = data[permid]['d']
            string = '%.1f %s %s %d %d' % (timestamp,
             permid_from,
             permid_for_user(permid),
             u,
             d)
            log.write(string + '\n')
            print >> sys.stderr, string

        totals = msg_data.get('totals', None)
        if totals != None:
            u, d = totals
            string = '%.1f TOT %s %d %d' % (timestamp,
             permid_from,
             u,
             d)
            log.write(string + '\n')
            print >> sys.stderr, string
        log.close()
