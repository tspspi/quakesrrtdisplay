import paho.mqtt.client as mqtt

from pathlib import Path
import os

import logging
import json
import math

import random

from datetime import datetime

import PySimpleGUI as sg
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, FigureCanvasAgg
from matplotlib.figure import Figure


class simulatedMessage:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload

class MQTTPatternMatcher:
    def __init__(self):
        self._handlers = []
        self._idcounter = 0

    def registerHandler(self, pattern, handler):
        self._idcounter = self._idcounter + 1
        self._handlers.append({ 'id' : self._idcounter, 'pattern' : pattern, 'handler' : handler })
        return self._idcounter

    def removeHandler(self, handlerId):
        newHandlerList = []
        for entry in self._handlers:
            if entry['id'] == handlerId:
                continue
            newHandlerList.append(entry)
        self._handlers = newHandlerList

    def _checkTopicMatch(self, filter, topic):
        filterparts = filter.split("/")
        topicparts = topic.split("/")

        # If last part of topic or filter is empty - drop ...
        if topicparts[-1] == "":
            del topicparts[-1]
        if filterparts[-1] == "":
            del filterparts[-1]

        # If filter is longer than topics we cannot have a match
        if len(filterparts) > len(topicparts):
            return False

        # Check all levels till we have a mistmatch or a multi level wildcard match,
        # continue scanning while we have a correct filter and no multi level match
        for i in range(len(filterparts)):
            if filterparts[i] == '+':
                continue
            if filterparts[i] == '#':
                return True
            if filterparts[i] != topicparts[i]:
                return False

        # Topic applies
        return True

    def callHandlers(self, topic, message):
        for regHandler in self._handlers:
            if self._checkTopicMatch(regHandler['pattern'], topic):
                regHandler['handler'](message)



class ModalDialogError:
    def __init__(self):
        pass

    def show(self, title, message):
        layout = [
            [ sg.Text(message) ],
            [ sg.Button("Ok", key="btnOk") ]
        ]
        window = sg.Window(title, layout, finalize=True)

        window.TKroot.transient()
        window.TKroot.grab_set()
        window.TKroot.focus_force()

        while True:
            event, values = window.read()
            if event in ('btnOk', None):
                window.close()
                return None


class WindowConnect:
    def __init__(self):
        pass

    def showConnect(self):
        defaults = {
            'broker' : '',
            'port' : '',
            'user' : '',
            'password' : '',
            'basetopic' : ''
        }

        try:
            with open(os.path.join(Path.home(), ".config/quakesrdisplay/connection.conf")) as cfgCon:
                defaults = json.load(cfgCon)
        except FileNotFoundError:
            pass

        layout = [
            [
                sg.Column([
                    [ sg.Text("MQTT broker:") ],
                    [ sg.Text("MQTT port:") ],
                    [ sg.Text("MQTT user:") ],
                    [ sg.Text("MQTT password:") ],
                    [ sg.Text("Base topic:") ]
                ]),
                sg.Column([
                    [ sg.InputText(defaults['broker'], key="txtBroker") ],
                    [ sg.InputText(defaults['port'], key="txtBrokerPort") ],
                    [ sg.InputText(defaults['user'], key="txtBrokerUser") ],
                    [ sg.InputText(defaults['password'], key="txtBrokerPassword") ],
                    [ sg.InputText(defaults['basetopic'], key="txtBasetopic") ]
                ]),
            ],
            [
                sg.Button("Connect", key="btnConnect"),
                sg.Button("Exit", key="btnAbort")
            ]
        ]

        window = sg.Window("QUAK/ESR realtime display", layout, finalize=True)

        while True:
            event, values = window.read(timeout = 10)
            if event in ('btnAbort', None):
                return None
            if event == 'btnConnect':
                brokername = values['txtBroker']
                brokerport = None
                try:
                    brokerport = int(values['txtBrokerPort'])
                except ValueError:
                    ModalDialogError().show("Invalid broker port", "The supplied broker port is invalid")
                brokeruser = values['txtBrokerUser']
                brokerpass = values['txtBrokerPassword']
                basetopic = values['txtBasetopic']

                window.close()

                return {
                    'broker' : brokername,
                    'port' : brokerport,
                    'user' : brokeruser,
                    'pass' : brokerpass ,
                    'basetopic' : basetopic
                }

class QUAKESRRealtimeDisplay:
    def __init__(
        self,
        connectionData,

        plotsize = (320, 240)
    ):
        self._condata = connectionData
        if self._condata['basetopic'][-1] != '/':
            self._condata['basetopic'] = self._condata['basetopic'] + "/"

        self._plotsize = plotsize
        self._statusstring = "Not connected"
        self._lastscan = { 'start' : "", 'stop' : "", 'duration' : "", 'type' : "" }
        self._mqttHandlers = MQTTPatternMatcher()

        self._mqttHandlers.registerHandler(f"{self._condata['basetopic']}scan/peak/peakdata", self._msghandler_received_peakdata)
        self._mqttHandlers.registerHandler(f"{self._condata['basetopic']}scan/peak/zeropeakdata", self._msghandler_received_zeropeakdata)
        self._mqttHandlers.registerHandler(f"{self._condata['basetopic']}scan/+/start", self._msghandler_received_startscan)
        self._mqttHandlers.registerHandler(f"{self._condata['basetopic']}scan/+/done", self._msghandler_received_donescan)

        self._mqttHandlers.registerHandler(f"{self._condata['basetopic']}scan/until/+/start", self._msghandler_received_startscan)
        self._mqttHandlers.registerHandler(f"{self._condata['basetopic']}scan/until/+/done", self._msghandler_received_donescan)

        self._mqttHandlers.registerHandler(f"{self._condata['basetopic']}scanuntil/start", self._msghandler_resetandenableaverage)
        self._mqttHandlers.registerHandler(f"{self._condata['basetopic']}scanuntil/done", self._msghandler_stoprunningaverage)

        self._mqttHandlers.registerHandler(f"{self._condata['basetopic']}egun/beamcurrent/estimate", self._msghandler_beamcurrentestimate)

        self._showDiffInSigma = False

        self._lastPeakData = {
            'I' : None,
            'sig' : None,
            'err' : None,
            'sigZero' : None,
            'errZero' : None,
            'sigDiff' : None,
            'errDiff' : None,
            'n' : None,
            'changed' : False
        }

        self._scanDurations = []
        self._scanDurationsUpdated = True

        self._ebeamCurrentEst = []
        self._ebeamCurrentMeas = []
        self._ebeamUpdated = True

        self._runningAverageInit()

    def _msghandler_received_startscan(self, message):
        self._lastscan['start'] = message.payload['starttime']
        self._lastscan['stop'] = ""
        self._lastscan['duration'] = ""

    def _msghandler_received_donescan(self, message):
        self._lastscan['start'] = message.payload['starttime'].replace("_", " ")
        self._lastscan['stop'] = message.payload['endtime'].replace("_", " ")

        stime = datetime.strptime(message.payload['starttime'], "%Y-%m-%d_%H:%M:%S")
        etime = datetime.strptime(message.payload['endtime'], "%Y-%m-%d_%H:%M:%S")

        self._lastscan['duration'] = str((etime-stime).total_seconds()) + "s (" + str(etime - stime) + ")"
        self._scanDurations.append((etime-stime).total_seconds())
        self._scanDurationsUpdated = True

    def _msghandler_beamcurrentestimate(self, message):
        try:
            self._ebeamCurrentEst.append(float(message.payload['current']))
            self._ebeamUpdated = True
        except:
            pass

    def _msghandler_received_peakdata(self, message):
        currents = []
        qAvg = []
        iAvg = []
        qErr = None
        iErr = None
        n = 0

        for ptCurrent in message.payload['payload']:
            currents.append(ptCurrent[0])

            # Average q's and average i's
            n = int((len(ptCurrent)-1) / 2)
            qSum = 0
            iSum = 0
            for ptIteration in range(n):
                iSum = iSum + ptCurrent[1 + ptIteration] / float(n)
                qSum = qSum + ptCurrent[1 + n + ptIteration] / float(n)
            qAvg.append(qSum)
            iAvg.append(iSum)

            # Error bars ...
            qE = 0
            iE = 0
            if n > 1:
                if qErr is None:
                    qErr = []
                    iErr = []

                for ptIteration in range(n):
                    iE = iE + (ptCurrent[1 + ptIteration] - iSum)*(ptCurrent[1 + ptIteration] - iSum) / float(n)
                    qE = qE + (ptCurrent[1 + ptIteration + n] - qSum)*(ptCurrent[1 + ptIteration + n] - qSum) / float(n)
                iE = math.sqrt(iE)
                qE = math.sqrt(qE)
                iErr.append(iE)
                qErr.append(qE)

        # Update local cache ...
        self._lastPeakData['I'] = currents
        self._lastPeakData['n'] = n
        self._lastPeakData['sig'] = { 'i' : iAvg, 'q' : qAvg }
        self._lastPeakData['err'] = { 'i' : iErr, 'q' : qErr }
        self._lastPeakData['changed'] = True

        # Update running average if required
        self._runningAverageUpdate(message, False)


    def _runningAverageInit(self):
        self._averagedPeakData = {
            'I' : None,
            'sig' : None,
            'err' : None,
            'sigZero' : None,
            'errZero' : None,
            'sigDiff' : None,
            'errDiff' : None,
            'n' : None,
            'changed' : True,

            'enabled' : True
        }

        self._runningAverageData = {
            'I' : None,
            'sigAverages' : {
                'i' : None,
                'q' : None
            },
            'sigM2' : {
                'i' : None,
                'q' : None
            },
            'zeroAverages' : {
                'i' : None,
                'q' : None
            },
            'zeroM2' : {
                'i' : None,
                'q' : None
            },
            'ntotal' : 0,
            'ntotalZero' : 0
        }

    def _runningAverageUpdate(self, message, isZero = False):
        if not self._averagedPeakData['enabled']:
            return

        if not isZero:
            nNew = int((len(message.payload['payload'][0])-1) / 2)
            updateCurrentValues = False
            for ptIteration in range(nNew):
                for ptCurrent in range(len(message.payload['payload'])):
                    curCurrent = message.payload['payload'][ptCurrent][0]
                    curISamp = message.payload['payload'][ptCurrent][1 + ptIteration]
                    curQSamp = message.payload['payload'][ptCurrent][1 + nNew + ptIteration]

                    if self._runningAverageData['ntotal'] == 0:
                        if self._runningAverageData['I'] is None:
                            self._runningAverageData['I'] = [ ]
                            updateCurrentValues = True
                        if updateCurrentValues:
                            self._runningAverageData['I'].append(curCurrent)
                        if self._runningAverageData['sigAverages']['i'] is None:
                            self._runningAverageData['sigAverages']['i'] = []
                            self._runningAverageData['sigAverages']['q'] = []
                            self._runningAverageData['sigM2']['i'] = []
                            self._runningAverageData['sigM2']['q'] = []
                        #self._runningAverageData['I'].append(curCurrent)
                        self._runningAverageData['sigAverages']['i'].append(curISamp)
                        self._runningAverageData['sigAverages']['q'].append(curQSamp)
                        self._runningAverageData['sigM2']['i'].append(0.0)
                        self._runningAverageData['sigM2']['q'].append(0.0)
                    else:
                        # Update running average & moments
                        oldAvgI = self._runningAverageData['sigAverages']['i'][ptCurrent]
                        oldAvgQ = self._runningAverageData['sigAverages']['q'][ptCurrent]
                        newAvgI = oldAvgI * (self._runningAverageData['ntotal'] / (self._runningAverageData['ntotal'] + 1)) + 1.0 / (self._runningAverageData['ntotal'] + 1) * curISamp
                        newAvgQ = oldAvgQ * (self._runningAverageData['ntotal'] / (self._runningAverageData['ntotal'] + 1)) + 1.0 / (self._runningAverageData['ntotal'] + 1) * curQSamp
                        self._runningAverageData['sigAverages']['i'][ptCurrent] = newAvgI
                        self._runningAverageData['sigAverages']['q'][ptCurrent] = newAvgQ
                        self._runningAverageData['sigM2']['i'][ptCurrent] = self._runningAverageData['sigM2']['i'][ptCurrent] + (curISamp - oldAvgI)*(curISamp - newAvgI)
                        self._runningAverageData['sigM2']['q'][ptCurrent] = self._runningAverageData['sigM2']['q'][ptCurrent] + (curQSamp - oldAvgQ)*(curQSamp - newAvgQ)
                self._runningAverageData['ntotal'] = self._runningAverageData['ntotal'] + 1

                self._averagedPeakData['I'] = self._runningAverageData['I']
                self._averagedPeakData['sig'] = self._runningAverageData['sigAverages']

                newError = { 'i' : [], 'q' : [] }
                for i in range(len(self._runningAverageData['sigM2']['i'])):
                    newError['i'].append(math.sqrt(self._runningAverageData['sigM2']['i'][i] / self._runningAverageData['ntotal']))
                    newError['q'].append(math.sqrt(self._runningAverageData['sigM2']['q'][i] / self._runningAverageData['ntotal']))
                self._averagedPeakData['err'] = newError
                self._averagedPeakData['changed'] = True
        else:
            nNew = int((len(message.payload['payload'][0])-1) / 2)
            updateCurrentValues = False
            for ptIteration in range(nNew):
                for ptCurrent in range(len(message.payload['payload'])):
                    curCurrent = message.payload['payload'][ptCurrent][0]
                    curISamp = message.payload['payload'][ptCurrent][1 + ptIteration]
                    curQSamp = message.payload['payload'][ptCurrent][1 + nNew + ptIteration]

                    if self._runningAverageData['ntotalZero'] == 0:
                        if self._runningAverageData['I'] is None:
                            self._runningAverageData['I'] = []
                            updateCurrentValues = True
                        if updateCurrentValues:
                            self._runningAverageData['I'].append(curCurrent)
                        if self._runningAverageData['zeroAverages']['i'] is None:
                            self._runningAverageData['zeroAverages']['i'] = []
                            self._runningAverageData['zeroAverages']['q'] = []
                            self._runningAverageData['zeroM2']['i'] = []
                            self._runningAverageData['zeroM2']['q'] = []
                        self._runningAverageData['zeroAverages']['i'].append(curISamp)
                        self._runningAverageData['zeroAverages']['q'].append(curQSamp)
                        self._runningAverageData['zeroM2']['i'].append(0.0)
                        self._runningAverageData['zeroM2']['q'].append(0.0)
                    else:
                        # Update running average & moments
                        oldAvgI = self._runningAverageData['zeroAverages']['i'][ptCurrent]
                        oldAvgQ = self._runningAverageData['zeroAverages']['q'][ptCurrent]
                        newAvgI = self._runningAverageData['zeroAverages']['i'][ptCurrent] * (self._runningAverageData['ntotalZero'] / (self._runningAverageData['ntotalZero'] + 1)) + 1.0 / (self._runningAverageData['ntotalZero'] + 1) * curISamp
                        newAvgQ = self._runningAverageData['zeroAverages']['q'][ptCurrent] * (self._runningAverageData['ntotalZero'] / (self._runningAverageData['ntotalZero'] + 1)) + 1.0 / (self._runningAverageData['ntotalZero'] + 1) * curQSamp
                        self._runningAverageData['zeroAverages']['i'][ptCurrent] = newAvgI
                        self._runningAverageData['zeroAverages']['q'][ptCurrent] = newAvgQ
                        self._runningAverageData['zeroM2']['i'][ptCurrent] = self._runningAverageData['zeroM2']['i'][ptCurrent] + (curISamp - oldAvgI)*(curISamp - newAvgI)
                        self._runningAverageData['zeroM2']['q'][ptCurrent] = self._runningAverageData['zeroM2']['q'][ptCurrent] + (curQSamp - oldAvgQ)*(curQSamp - newAvgQ)
                self._runningAverageData['ntotalZero'] = self._runningAverageData['ntotalZero'] + 1

                self._averagedPeakData['I'] = self._runningAverageData['I']
                self._averagedPeakData['sigZero'] = self._runningAverageData['zeroAverages']

                newError = { 'i' : [], 'q' : [] }
                for i in range(len(self._runningAverageData['zeroM2']['i'])):
                    newError['i'].append(math.sqrt(self._runningAverageData['zeroM2']['i'][i] / self._runningAverageData['ntotalZero']))
                    newError['q'].append(math.sqrt(self._runningAverageData['zeroM2']['q'][i] / self._runningAverageData['ntotalZero']))
                self._averagedPeakData['errZero'] = newError
                self._averagedPeakData['changed'] = True

        # ToDo: Differences
        iDiff = None
        qDiff = None
        iDiffErr = None
        qDiffErr = None
        if self._averagedPeakData['sig'] and self._averagedPeakData['sig']['i'] and self._averagedPeakData['sigZero'] and self._averagedPeakData['sigZero']['i']:
            if (len(self._averagedPeakData['sig']['i']) == len(self._averagedPeakData['sigZero']['i'])) and (len(self._lastPeakData['sig']['q']) == len(self._averagedPeakData['sigZero']['q'])):
                iDiff = []
                qDiff = []
                iDiffErr = None
                qDiffErr = None

                for i in range(len(self._averagedPeakData['sig']['i'])):
                    iDiff.append(self._averagedPeakData['sig']['i'][i] - self._averagedPeakData['sigZero']['i'][i])
                    qDiff.append(self._averagedPeakData['sig']['q'][i] - self._averagedPeakData['sigZero']['q'][i])

                    if (not (self._averagedPeakData['err'] is None)) and (not (self._averagedPeakData['err']['i'] is None)) and (not (self._averagedPeakData['errZero'] is None)) and (not (self._averagedPeakData['errZero']['i'] is None)):
                        if iDiffErr is None:
                            iDiffErr = []
                            qDiffErr = []
                        iDiffErr.append(math.sqrt(self._averagedPeakData['errZero']['i'][i]*self._averagedPeakData['errZero']['i'][i] + self._averagedPeakData['err']['i'][i]*self._averagedPeakData['err']['i'][i]))
                        qDiffErr.append(math.sqrt(self._averagedPeakData['errZero']['q'][i]*self._averagedPeakData['errZero']['q'][i] + self._averagedPeakData['err']['q'][i]*self._averagedPeakData['err']['q'][i]))

        self._averagedPeakData['sigDiff'] = { 'i' : iDiff, 'q' : qDiff }
        self._averagedPeakData['errDiff'] = { 'i' : iDiffErr, 'q' : qDiffErr }
        self._averagedPeakData['changed'] = True


    def _msghandler_resetandenableaverage(self, message):
        self._runningAverageInit()
        self._averagedPeakData['enabled'] = True
        # self._window['chkRunAverage'].Update(True)
        self._window.write_event_value("sigEnableAverage", "*")
    def _msghandler_stoprunningaverage(self, message):
        self._averagedPeakData['enabled'] = False
        #self._window['chkRunAverage'].Update(False)
        self._window.write_event_value("sigDisableAverage", "*")

    def _msghandler_received_zeropeakdata(self, message):
        currents = []
        qAvg = []
        iAvg = []
        qErr = None
        iErr = None
        n = 0

        for ptCurrent in message.payload['payload']:
            currents.append(ptCurrent[0])

            # Average q's and average i's
            n = int((len(ptCurrent)-1) / 2)
            qSum = 0
            iSum = 0
            for ptIteration in range(n):
                iSum = iSum + ptCurrent[1 + ptIteration] / float(n)
                qSum = qSum + ptCurrent[1 + n + ptIteration] / float(n)
            qAvg.append(qSum)
            iAvg.append(iSum)

            # Error bars ...
            qE = 0
            iE = 0
            if n > 1:
                if qErr is None:
                    qErr = []
                    iErr = []

                for ptIteration in range(n):
                    iE = iE + (ptCurrent[1 + ptIteration] - iSum)*(ptCurrent[1 + ptIteration] - iSum) / float(n)
                    qE = qE + (ptCurrent[1 + ptIteration + n] - qSum)*(ptCurrent[1 + ptIteration + n] - qSum) / float(n)
                iE = math.sqrt(iE)
                qE = math.sqrt(qE)
                iErr.append(iE)
                qErr.append(qE)

        # Calculate difference if possible
        if self._lastPeakData['sig']:
            if (len(self._lastPeakData['sig']['i']) == len(iAvg)) and (len(self._lastPeakData['sig']['q']) == len(qAvg)):
                iDiff = []
                qDiff = []
                iDiffErr = None
                qDiffErr = None

                for i in range(len(iAvg)):
                    iDiff.append(self._lastPeakData['sig']['i'][i] - iAvg[i])
                    qDiff.append(self._lastPeakData['sig']['q'][i] - qAvg[i])

                    if (n > 1) and (not (self._lastPeakData['err'] is None)):
                        if iDiffErr is None:
                            iDiffErr = []
                            qDiffErr = []
                        iDiffErr.append(math.sqrt(iErr[i]*iErr[i] + self._lastPeakData['err']['i'][i]*self._lastPeakData['err']['i'][i]))
                        qDiffErr.append(math.sqrt(qErr[i]*qErr[i] + self._lastPeakData['err']['q'][i]*self._lastPeakData['err']['q'][i]))
        else:
            iDiff = None
            qDiff = None
            iDiffErr = None
            qDiffErr = None

        # Update local cache ...
        self._lastPeakData['I'] = currents
        self._lastPeakData['n'] = n
        self._lastPeakData['sigZero'] = { 'i' : iAvg, 'q' : qAvg }
        self._lastPeakData['errZero'] = { 'i' : iErr, 'q' : qErr }
        self._lastPeakData['sigDiff'] = { 'i' : iDiff, 'q' : qDiff }
        self._lastPeakData['errDiff'] = { 'i' : iDiffErr, 'q' : qDiffErr }
        self._lastPeakData['changed'] = True

        # Update running average if required

        self._runningAverageUpdate(message, True)

    def _mqtt_on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._statusstring = "Connected to {}:{} as {}".format(self._condata['broker'], self._condata['port'], self._condata['user'])

            #Subscribe all messages in our basetopic
            client.subscribe(self._condata['basetopic']+"#")
        else:
            self._statusstring = "Failed connecting to {}:{} as {}, retrying".format(self._condata['broker'], self._condata['port'], self._condata['user'])
        pass
    def _mqtt_on_message(self, client, userdata, msg):
        logging.debug("[MQTT IN] {}: {}".format(msg.topic, msg.payload))
        try:
            msg.payload = json.loads(str(msg.payload.decode('utf-8', 'ignore')))
        except:
            # Ignore if we don't have a JSON payload
            pass
        self._mqttHandlers.callHandlers(msg.topic, msg)

    def __init_figure(self, canvasName, xlabel, ylabel, title, grid=True):
        figTemp = Figure()
        fig = Figure(figsize = (self._plotsize[0] / figTemp.get_dpi(), self._plotsize[1] / figTemp.get_dpi()))

        ax = fig.add_subplot(111)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_title(title)

        if grid:
            ax.grid()
        fig_agg = FigureCanvasTkAgg(fig, self._window[canvasName].TKCanvas)
        fig_agg.draw()
        fig_agg.get_tk_widget().pack(side='top', fill='both', expand=1)

        return {
            'figure' : fig,
            'axis' : ax,
            'fig_agg' : fig_agg,
            'xlabel' : xlabel,
            'ylabel' : ylabel,
            'title' : title
        }

    def redrawAveragedData(self):
        data = self._averagedPeakData
        if not data['changed']:
            return

        self._averagedPeakData['changed'] = False

        self._figures['sigAvg']['axis'].cla()
        self._figures['sigAvg']['axis'].grid()

        if not(data['sig'] is None) and not(data['sig']['i'] is None):
            if not(data['err'] is None) and not(data['err']['i'] is None):
                # Plot with error bars ...
                self._figures['sigAvg']['axis'].errorbar(data['I'], data['sig']['i'], yerr = data['err']['i'], label = "I")
                self._figures['sigAvg']['axis'].errorbar(data['I'], data['sig']['q'], yerr = data['err']['q'], label = "Q")
                self._figures['sigAvg']['axis'].legend()
            else:
                self._figures['sigAvg']['axis'].plot(data['I'], data['sig']['i'], label = "I")
                self._figures['sigAvg']['axis'].plot(data['I'], data['sig']['q'], label = "Q")
                self._figures['sigAvg']['axis'].legend()

        self._figures['sigAvg']['axis'].set_xlabel(self._figures['sigAvg']['xlabel'])
        self._figures['sigAvg']['axis'].set_ylabel(self._figures['sigAvg']['ylabel'])
        self._figures['sigAvg']['axis'].set_title(self._figures['sigAvg']['title'])
        self._figures['sigAvg']['fig_agg'].draw()

        # Error plot (signal)

        self._figures['errAvg']['axis'].cla()
        self._figures['errAvg']['axis'].grid()

        if not(data['err'] is None) and not(data['err']['i'] is None):
            self._figures['errAvg']['axis'].plot(data['I'], data['err']['i'], label="I")
            self._figures['errAvg']['axis'].plot(data['I'], data['err']['q'], label="Q")
            self._figures['errAvg']['axis'].legend()

        self._figures['errAvg']['axis'].set_xlabel(self._figures['errAvg']['xlabel'])
        self._figures['errAvg']['axis'].set_ylabel(self._figures['errAvg']['ylabel'])
        self._figures['errAvg']['axis'].set_title(self._figures['errAvg']['title'])
        self._figures['errAvg']['fig_agg'].draw()

        self._figures['sigZeroAvg']['axis'].cla()
        self._figures['sigZeroAvg']['axis'].grid()

        if not (data['sigZero'] is None):
            if not (data['errZero'] is None) and not (data['errZero']['i'] is None):
                self._figures['sigZeroAvg']['axis'].errorbar(data['I'], data['sigZero']['i'], yerr = data['errZero']['i'], label = "I")
                self._figures['sigZeroAvg']['axis'].errorbar(data['I'], data['sigZero']['q'], yerr = data['errZero']['q'], label = "Q")
                self._figures['sigZeroAvg']['axis'].legend()
            else:
                self._figures['sigZeroAvg']['axis'].plot(data['I'], data['sigZero']['i'], label = "I")
                self._figures['sigZeroAvg']['axis'].plot(data['I'], data['sigZero']['q'], label = "Q")
                self._figures['sigZeroAvg']['axis'].legend()

        self._figures['sigZeroAvg']['axis'].set_xlabel(self._figures['sigZeroAvg']['xlabel'])
        self._figures['sigZeroAvg']['axis'].set_ylabel(self._figures['sigZeroAvg']['ylabel'])
        self._figures['sigZeroAvg']['axis'].set_title(self._figures['sigZeroAvg']['title'])
        self._figures['sigZeroAvg']['fig_agg'].draw()

        # Error plot
        self._figures['errZeroAvg']['axis'].cla()
        self._figures['errZeroAvg']['axis'].grid()

        if not (data['errZero'] is None) and not (data['errZero']['i'] is None):
            self._figures['errZeroAvg']['axis'].plot(data['I'], data['errZero']['i'], label="I")
            self._figures['errZeroAvg']['axis'].plot(data['I'], data['errZero']['q'], label="Q")
            self._figures['errZeroAvg']['axis'].legend()

        self._figures['errZeroAvg']['axis'].set_xlabel(self._figures['errZeroAvg']['xlabel'])
        self._figures['errZeroAvg']['axis'].set_ylabel(self._figures['errZeroAvg']['ylabel'])
        self._figures['errZeroAvg']['axis'].set_title(self._figures['errZeroAvg']['title'])
        self._figures['errZeroAvg']['fig_agg'].draw()

        self._figures['sigDiffAvg']['axis'].cla()
        self._figures['sigDiffAvg']['axis'].grid()

        # if self._lastPeakData['n'] > 1:
        if not (data['sigDiff'] is None) and not (data['sigDiff']['i'] is None):
            if not self._showDiffInSigma:
                if (not (self._averagedPeakData['errDiff'] is None)) and (not (self._averagedPeakData['errDiff']['i'] is None)):
                    self._figures['sigDiffAvg']['axis'].errorbar(data['I'], data['sigDiff']['i'], yerr = data['errDiff']['i'], label = "I")
                    self._figures['sigDiffAvg']['axis'].errorbar(data['I'], data['sigDiff']['q'], yerr = data['errDiff']['q'], label = "Q")
                    self._figures['sigDiffAvg']['axis'].legend()
                else:
                    self._figures['sigDiffAvg']['axis'].plot(data['I'], data['sigDiff']['i'], label = "I")
                    self._figures['sigDiffAvg']['axis'].plot(data['I'], data['sigDiff']['q'], label = "Q")
                    self._figures['sigDiffAvg']['axis'].legend()
            else:
                if (not (self._averagedPeakData['errDiff'] is None)) and (not (self._averagedPeakData['errDiff']['i'] is None)):
                    di = [a_i / b_i for a_i, b_i in zip(data['sigDiff']['i'], data['errDiff']['i'])]
                    dq = [a_i / b_i for a_i, b_i in zip(data['sigDiff']['q'], data['errDiff']['q'])]
                    self._figures['sigDiffAvg']['axis'].plot(data['I'], di, label = "I")
                    self._figures['sigDiffAvg']['axis'].plot(data['I'], dq, label = "Q")
                    self._figures['sigDiffAvg']['axis'].legend()

        self._figures['sigDiffAvg']['axis'].set_xlabel(self._figures['sigDiffAvg']['xlabel'])
        self._figures['sigDiffAvg']['axis'].set_ylabel(self._figures['sigDiffAvg']['ylabel'])
        self._figures['sigDiffAvg']['axis'].set_title(self._figures['sigDiffAvg']['title'])
        self._figures['sigDiffAvg']['fig_agg'].draw()

        # Error plot
        self._figures['errDiffAvg']['axis'].cla()
        self._figures['errDiffAvg']['axis'].grid()

        if (not (self._averagedPeakData['errDiff'] is None)) and (not (self._averagedPeakData['errDiff']['i'] is None)):
            self._figures['errDiffAvg']['axis'].plot(data['I'], data['errDiff']['i'], label="I")
            self._figures['errDiffAvg']['axis'].plot(data['I'], data['errDiff']['q'], label="Q")
            self._figures['errDiffAvg']['axis'].legend()

        self._figures['errDiffAvg']['axis'].set_xlabel(self._figures['errDiffAvg']['xlabel'])
        self._figures['errDiffAvg']['axis'].set_ylabel(self._figures['errDiffAvg']['ylabel'])
        self._figures['errDiffAvg']['axis'].set_title(self._figures['errDiffAvg']['title'])
        self._figures['errDiffAvg']['fig_agg'].draw()

    def redrawPeakData(self):
        data = self._lastPeakData
        if not data['changed']:
            return

        self._lastPeakData['changed'] = False

        if data['n'] is None:
            return

        if not (data['sig'] is None):
            self._figures['sig']['axis'].cla()
            self._figures['sig']['axis'].grid()

            if self._lastPeakData['n'] > 1:
                self._figures['sig']['axis'].errorbar(data['I'], data['sig']['i'], yerr = data['err']['i'], label = "I")
                self._figures['sig']['axis'].errorbar(data['I'], data['sig']['q'], yerr = data['err']['q'], label = "Q")
                self._figures['sig']['axis'].legend()
            else:
                self._figures['sig']['axis'].plot(data['I'], data['sig']['i'], label = "I")
                self._figures['sig']['axis'].plot(data['I'], data['sig']['q'], label = "Q")
                self._figures['sig']['axis'].legend()

            self._figures['sig']['axis'].set_xlabel(self._figures['sig']['xlabel'])
            self._figures['sig']['axis'].set_ylabel(self._figures['sig']['ylabel'])
            self._figures['sig']['axis'].set_title(self._figures['sig']['title'])
            self._figures['sig']['fig_agg'].draw()

            # Error plot
            self._figures['err']['axis'].cla()
            self._figures['err']['axis'].grid()

            if data['n'] > 1:
                self._figures['err']['axis'].plot(data['I'], data['err']['i'], label="I")
                self._figures['err']['axis'].plot(data['I'], data['err']['q'], label="Q")
                self._figures['err']['axis'].legend()

            self._figures['err']['axis'].set_xlabel(self._figures['err']['xlabel'])
            self._figures['err']['axis'].set_ylabel(self._figures['err']['ylabel'])
            self._figures['err']['axis'].set_title(self._figures['err']['title'])
            self._figures['err']['fig_agg'].draw()

        if not (data['sigZero'] is None):
            self._figures['sigZero']['axis'].cla()
            self._figures['sigZero']['axis'].grid()

            if self._lastPeakData['n'] > 1:
                self._figures['sigZero']['axis'].errorbar(data['I'], data['sigZero']['i'], yerr = data['errZero']['i'], label = "I")
                self._figures['sigZero']['axis'].errorbar(data['I'], data['sigZero']['q'], yerr = data['errZero']['q'], label = "Q")
                self._figures['sigZero']['axis'].legend()
            else:
                self._figures['sigZero']['axis'].plot(data['I'], data['sigZero']['i'], label = "I")
                self._figures['sigZero']['axis'].plot(data['I'], data['sigZero']['q'], label = "Q")
                self._figures['sigZero']['axis'].legend()

            self._figures['sigZero']['axis'].set_xlabel(self._figures['sigZero']['xlabel'])
            self._figures['sigZero']['axis'].set_ylabel(self._figures['sigZero']['ylabel'])
            self._figures['sigZero']['axis'].set_title(self._figures['sigZero']['title'])
            self._figures['sigZero']['fig_agg'].draw()

            # Error plot
            self._figures['errZero']['axis'].cla()
            self._figures['errZero']['axis'].grid()

            if data['n'] > 1:
                self._figures['errZero']['axis'].plot(data['I'], data['errZero']['i'], label="I")
                self._figures['errZero']['axis'].plot(data['I'], data['errZero']['q'], label="Q")
                self._figures['errZero']['axis'].legend()

            self._figures['errZero']['axis'].set_xlabel(self._figures['errZero']['xlabel'])
            self._figures['errZero']['axis'].set_ylabel(self._figures['errZero']['ylabel'])
            self._figures['errZero']['axis'].set_title(self._figures['errZero']['title'])
            self._figures['errZero']['fig_agg'].draw()

        if not (data['sigDiff'] is None) and not (data['sigDiff']['i'] is None):
            self._figures['sigDiff']['axis'].cla()
            self._figures['sigDiff']['axis'].grid()

            # if self._lastPeakData['n'] > 1:
            if not self._showDiffInSigma:
                if (not (self._lastPeakData['errDiff'] is None)) and (not (self._lastPeakData['errDiff']['i'] is None)):
                    self._figures['sigDiff']['axis'].errorbar(data['I'], data['sigDiff']['i'], yerr = data['errDiff']['i'], label = "I")
                    self._figures['sigDiff']['axis'].errorbar(data['I'], data['sigDiff']['q'], yerr = data['errDiff']['q'], label = "Q")
                    self._figures['sigDiff']['axis'].legend()
                else:
                    self._figures['sigDiff']['axis'].plot(data['I'], data['sigDiff']['i'], label = "I")
                    self._figures['sigDiff']['axis'].plot(data['I'], data['sigDiff']['q'], label = "Q")
                    self._figures['sigDiff']['axis'].legend()
            else:
                if (not (self._lastPeakData['errDiff'] is None)) and (not (self._lastPeakData['errDiff']['i'] is None)):
                    di = [a_i / b_i for a_i, b_i in zip(data['sigDiff']['i'], data['errDiff']['i'])]
                    dq = [a_i / b_i for a_i, b_i in zip(data['sigDiff']['q'], data['errDiff']['q'])]
                    self._figures['sigDiff']['axis'].plot(data['I'], di, label = "I")
                    self._figures['sigDiff']['axis'].plot(data['I'], dq, label = "Q")
                    self._figures['sigDiff']['axis'].legend()

            self._figures['sigDiff']['axis'].set_xlabel(self._figures['sigDiff']['xlabel'])
            self._figures['sigDiff']['axis'].set_ylabel(self._figures['sigDiff']['ylabel'])
            self._figures['sigDiff']['axis'].set_title(self._figures['sigDiff']['title'])
            self._figures['sigDiff']['fig_agg'].draw()

            # Error plot
            self._figures['errDiff']['axis'].cla()
            self._figures['errDiff']['axis'].grid()

            if (not (self._lastPeakData['errDiff'] is None)) and (not (self._lastPeakData['errDiff']['i'] is None)):
                self._figures['errDiff']['axis'].plot(data['I'], data['errDiff']['i'], label="I")
                self._figures['errDiff']['axis'].plot(data['I'], data['errDiff']['q'], label="Q")
                self._figures['errDiff']['axis'].legend()

            self._figures['errDiff']['axis'].set_xlabel(self._figures['errDiff']['xlabel'])
            self._figures['errDiff']['axis'].set_ylabel(self._figures['errDiff']['ylabel'])
            self._figures['errDiff']['axis'].set_title(self._figures['errDiff']['title'])
            self._figures['errDiff']['fig_agg'].draw()

    def redrawScanDurations(self):
        if not self._scanDurationsUpdated:
            return
        self._scanDurationsUpdated = True

        self._figures['scanDurations']['axis'].cla()
        self._figures['scanDurations']['axis'].grid()
        if len(self._scanDurations) > 0:
            self._figures['scanDurations']['axis'].plot(self._scanDurations)
        self._figures['scanDurations']['axis'].set_xlabel(self._figures['scanDurations']['xlabel'])
        self._figures['scanDurations']['axis'].set_ylabel(self._figures['scanDurations']['ylabel'])
        self._figures['scanDurations']['axis'].set_title(self._figures['scanDurations']['title'])
        self._figures['scanDurations']['fig_agg'].draw()

    def redrawBeamCurrent(self):
        if not self._ebeamUpdated:
            return
        self._ebeamUpdated = False

        self._figures['ebeamCurrentEst']['axis'].cla()
        self._figures['ebeamCurrentEst']['axis'].grid()
        if len(self._ebeamCurrentEst) > 0:
            self._figures['ebeamCurrentEst']['axis'].plot(self._ebeamCurrentEst)
        self._figures['ebeamCurrentEst']['axis'].set_xlabel(self._figures['ebeamCurrentEst']['xlabel'])
        self._figures['ebeamCurrentEst']['axis'].set_ylabel(self._figures['ebeamCurrentEst']['ylabel'])
        self._figures['ebeamCurrentEst']['axis'].set_title(self._figures['ebeamCurrentEst']['title'])
        self._figures['ebeamCurrentEst']['fig_agg'].draw()

        self._figures['ebeamCurrentMeas']['axis'].cla()
        self._figures['ebeamCurrentMeas']['axis'].grid()
        if len(self._ebeamCurrentMeas) > 0:
            self._figures['ebeamCurrentMeas']['axis'].plot(self._ebeamCurrentMeas)
        self._figures['ebeamCurrentMeas']['axis'].set_xlabel(self._figures['ebeamCurrentMeas']['xlabel'])
        self._figures['ebeamCurrentMeas']['axis'].set_ylabel(self._figures['ebeamCurrentMeas']['ylabel'])
        self._figures['ebeamCurrentMeas']['axis'].set_title(self._figures['ebeamCurrentMeas']['title'])
        self._figures['ebeamCurrentMeas']['fig_agg'].draw()

    def run(self):
        # MQTT setup ...
        self.mqtt = mqtt.Client()
        self.mqtt.on_connect = self._mqtt_on_connect
        self.mqtt.on_message = self._mqtt_on_message

        self.mqtt.username_pw_set(self._condata['user'], self._condata['pass'])
        self.mqtt.connect(self._condata['broker'], self._condata['port'])
        self.mqtt.loop_start()

        layout = [
            [
                sg.TabGroup([[
                        sg.Tab('Last peak',[
                            [
                                sg.Column([
                                    [ sg.Text("Signal") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvSig') ],
                                    [ sg.Text("Error") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvErr') ]
                                ], scrollable=False),
                                sg.Column([
                                    [ sg.Text("Signal (Zero)") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvSigZero') ],
                                    [ sg.Text("Error (Zero)") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvErrZero') ],
                                ], scrollable=False),
                                sg.Column([
                                    [ sg.Text("Signal (Difference)") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvSigDiff') ],
                                    [ sg.Text("Error (Difference)") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvErrDiff') ]
                                ], scrollable=False)
                            ]
                        ]),
                        sg.Tab('Average',[
                            [
                                sg.Column([
                                    [ sg.Text("Signal") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvSigAVG') ],
                                    [ sg.Text("Error") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvErrAVG') ]
                                ], scrollable=False),
                                sg.Column([
                                    [ sg.Text("Signal (Zero)") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvSigZeroAVG') ],
                                    [ sg.Text("Error (Zero)") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvErrZeroAVG') ],
                                ], scrollable=False),
                                sg.Column([
                                    [ sg.Text("Signal (Difference)") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvSigDiffAVG') ],
                                    [ sg.Text("Error (Difference)") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvErrDiffAVG') ]
                                ], scrollable=False)
                            ]
                        ]),
                        sg.Tab('Scan duration', [
                            [
                                sg.Column([
                                    [ sg.Text("Scan duration") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvMeasDuration') ],
                                    [ sg.Button("Reset", key='btnResetMeasurementDuration')]
                                ])
                            ]
                        ]),
                        sg.Tab('Electron beam', [
                            [
                                sg.Column([
                                    [ sg.Text("Current (measured)") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvEbeamCurrentMeas') ],
                                    [ sg.Text("Current (estimated)") ],
                                    [ sg.Canvas(size=self._plotsize, key='canvEbeamCurrentEst') ]
                                ])
                            ]
                        ])
                ]])
            ],
            [
                sg.Column([
                    [ sg.Text("Status:") ]
                ]),
                sg.Column([
                    [ sg.Text("Not connected", key='txtStatus') ]
                ]),
                sg.Column([
                    [ sg.Text("Scan type:") ],
                    [ sg.Text("Scan started:") ],
                    [ sg.Text("Scan finished:") ],
                    [ sg.Text("Scan duration:") ]
                ]),
                sg.Column([
                    [ sg.Text("", key="txtScantype") ],
                    [ sg.Text("", key="txtLastScanStart") ],
                    [ sg.Text("", key="txtLastScanFinish") ],
                    [ sg.Text("", key="txtLastScanDuration") ]
                ]),
                sg.Column([
                    [ sg.Checkbox("Running average", default = False, key="chkRunAverage") ],
                    [ sg.Button("Reset running average", key="btnAvgReset") ],
                    [ sg.Button("Exit", key="btnExit") ]
                ])
                #]),
                #sg.Column([
                #    [ sg.Button("Simulate Peak", key="btnSimPeak") ],
                #    [ sg.Button("Simulate Zero Peak", key="btnSimZero") ]
                #])
            ]
        ]

        self._window = sg.Window("QUAK/ESR Realtime display", layout, size=(1024,750), finalize=True)
        # self._window.Maximize()

        # Create figures / keep track of canvas, etc. ...
        self._figures = {
            'sig' : self.__init_figure('canvSig', 'B0', 'uV', 'Last peak signal'),
            'err' : self.__init_figure('canvErr', 'B0', 'uV', 'Last peak error'),

            'sigZero' : self.__init_figure('canvSigZero', 'B0', 'uV', 'Last peak zero signal'),
            'errZero' : self.__init_figure('canvErrZero', 'B0', 'uV', 'Zero signal error'),

            'sigDiff' : self.__init_figure('canvSigDiff', 'B0', 'uV', 'Current signal difference'),
            'errDiff' : self.__init_figure('canvErrDiff', 'B0', 'uV', 'Current error difference'),



            'sigAvg' : self.__init_figure('canvSigAVG', 'B0', 'uV', 'Peak signal (averaged)'),
            'errAvg' : self.__init_figure('canvErrAVG', 'B0', 'uV', 'Error (averaged)'),

            'sigZeroAvg' : self.__init_figure('canvSigZeroAVG', 'B0', 'uV', 'Zero signal (averaged)'),
            'errZeroAvg' : self.__init_figure('canvErrZeroAVG', 'B0', 'uV', 'Zero error (averaged)'),

            'sigDiffAvg' : self.__init_figure('canvSigDiffAVG', 'B0', 'uV', 'Difference signal (averaged)'),
            'errDiffAvg' : self.__init_figure('canvErrDiffAVG', 'B0', 'uV', 'Difference error (averaged)'),

            'scanDurations' : self.__init_figure('canvMeasDuration', 'Scan', 'Duration [s]', 'Scan durations'),

            'ebeamCurrentMeas' : self.__init_figure('canvEbeamCurrentMeas', 'Scan', 'Current (uA)', 'Measured beam current'),
            'ebeamCurrentEst' : self.__init_figure('canvEbeamCurrentEst', 'Scan', 'Current (uA)', 'Estimated beam current')
        }

        # Show window and react to events ...
        while True:
            event, values = self._window.read(timeout = 1)
            if event in ('btnExit', None):
                break
            self._averagedPeakData['enabled'] = values['chkRunAverage']
            if event == "btnAvgReset":
                self._runningAverageInit()
            if event == "sigEnableAverage":
                self._window['chkRunAverage'].Update(True)
            if event == "sigDisableAverage":
                self._window['chkRunAverage'].Update(False)
            if event == "btnResetMeasurementDuration":
                self._scanDurations = []
                self._scanDurationsUpdated = True

            # Redraw peak data if required ...
            self.redrawPeakData()
            self.redrawAveragedData()
            self.redrawScanDurations()
            self.redrawBeamCurrent()

            # Update status string
            self._window['txtStatus'].Update(self._statusstring)
            self._window['txtLastScanStart'].Update(self._lastscan['start'])
            self._window['txtLastScanFinish'].Update(self._lastscan['stop'])
            self._window['txtLastScanDuration'].Update(self._lastscan['duration'])
            self._window['txtScantype'].Update(self._lastscan['type'])

def main():
    conResult = WindowConnect().showConnect()
    if conResult:
        disp = QUAKESRRealtimeDisplay(conResult).run()

if __name__ == "__main__":
    main()
