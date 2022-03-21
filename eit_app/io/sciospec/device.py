#!C:\Anaconda3\envs\py38_app python
# -*- coding: utf-8 -*-

"""  Classes and function to interact with the Sciospec EIT device

Copyright (C) 2021  David Metz

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>. """


from dataclasses import dataclass
from enum import Enum, auto
from logging import getLogger
from queue import Queue
from time import sleep
from typing import Any, Union

# from eit_app.update_event import DevStatus
from eit_app.io.sciospec.com_constants import (SUCCESS,
                                               CMD_GET_DEVICE_INFOS,
                                               CMD_GET_ETHERNET_CONFIG,
                                               CMD_GET_MEAS_SETUP,
                                               CMD_GET_OUTPUT_CONFIG,
                                               CMD_SET_ETHERNET_CONFIG,
                                               CMD_SET_MEAS_SETUP,
                                               CMD_SET_OUTPUT_CONFIG,
                                               CMD_SOFT_RESET,
                                               CMD_START_STOP_MEAS, 
                                               OP_BURST_COUNT,
                                               OP_CURRENT_STAMP, OP_DHCP,
                                               OP_EXC_AMPLITUDE,
                                               OP_EXC_FREQUENCIES,
                                               OP_EXC_PATTERN, OP_EXC_STAMP,
                                               OP_FRAME_RATE, OP_IP_ADRESS,
                                               OP_MAC_ADRESS, OP_NULL,
                                               OP_RESET_SETUP, OP_START_MEAS,
                                               OP_STOP_MEAS, OP_TIME_STAMP,

                                               SciospecCmd, SciospecOption,)
from eit_app.io.sciospec.interface import Interface, SciospecSerialInterface
from eit_app.io.sciospec.setup import SciospecSetup
from eit_app.update_event import (DevAvailables, DevSetup, DevStatus, FrameInfo,
                                  FrameProgress, MeasuringStatus, MeasuringStates)
from eit_app.io.sciospec.communicator import SciospecCommunicator
from glob_utils.flags.flag import MultiStatewSignal
from glob_utils.log.log import main_log
from glob_utils.msgbox import errorMsgBox, infoMsgBox, warningMsgBox


from glob_utils.thread_process.signal import Signal

from serial import (  # get from http://pyserial.sourceforge.net/
    PortNotOpenError, SerialException)

__author__ = "David Metz"
__copyright__ = "Copyright (c) 2021"
__credits__ = ["David Metz"]
__license__ = "GPLv3"
__version__ = "2.0.0"
__maintainer__ = "David Metz"
__email__ = "d.metz@tu-bs.de"
__status__ = "Production"

logger = getLogger(__name__)

NONE_DEVICE = "None Device"

################################################################################

################################################################################

class SciospecEITDevice:

    """Device Class should only provide simple function to use the device such:
    - get devices
    - connect
    - disconnect
    - start/pause/resume/stop meas
    - set/get setup
    - reset """

    n_channel:int # nb of Channel from the EIT device
    # all sciospec connected to local machine generated by self.get_devices
    sciospec_devices:dict
    # Actual name of the device
    device_name: str

    meas_status:MultiStatewSignal

    setup:SciospecSetup 
    serial_interface:SciospecSerialInterface
    
    # The communicator is charged to send cmd to the interface, sort and
    # manage the comunitation (ack, etc). it dispatch the data recivied on 
    # two different Signals new_rx_meas_stream/new_rx_setup_stream
    communicator:SciospecCommunicator

    to_dataset:Signal # use to transmit new rx_meas_stream or cmd to dataset.
    to_gui:Signal# use to transmit update to the gui.



    def __init__(self, n_channel:int = 32):

        self.n_channel = n_channel
        self.sciospec_devices = {}
        self.device_name: str = NONE_DEVICE
        self.meas_status= MultiStatewSignal(list(MeasuringStates))
        self.meas_status.change_state(MeasuringStates.IDLE)
        self.setup = SciospecSetup(self.n_channel)
        self.serial_interface=SciospecSerialInterface()
        self.communicator= SciospecCommunicator()
        self.to_dataset=Signal(self)
        self.to_gui=Signal(self)
        self.to_capture_dev=Signal(self)

        # all the errors from the interface are catch and send through this 
        # error signal, the error are then here handled. Some of then need 
        # action on the device itself
        self.serial_interface.error.connect(self._handle_interface_error)
        #send the new to be processed by the communicator
        self.serial_interface.new_rx_frame.connect(self.communicator.add_rx_frame)
        # output signal of the communicator
        self.communicator.new_rx_meas_stream.connect(self.emit_to_dataset)
        self.communicator.new_rx_setup_stream.connect(self.setup.set_data)
        self.meas_status.changed.connect(self.emit_meas_status)

    ## =========================================================================
    ##  Methods to update gui
    ## =========================================================================    
    
    def emit_meas_status(self)->None:
        self.emit_to_gui(MeasuringStatus(self.meas_status.state))
        self.emit_to_capture_dev()
    
    def emit_dev_status(self)->None:
        self.emit_to_gui(DevStatus(self.is_connected, self.connect_prompt))

    def emit_to_gui(self, data:Any)->None:
        kwargs={"update_gui_data": data}
        self.to_gui.fire(None, **kwargs)
    
    def emit_to_capture_dev(self)->None:
        status= self.is_measuring or self.is_paused
        kwargs={"meas_status_dev": status}
        self.to_capture_dev.fire(None, **kwargs)

    ## =========================================================================
    ##  Methods for dataset
    ## =========================================================================    
    
    def emit_to_dataset(self, **kwargs)->None:
        self.to_dataset.fire(None, **kwargs)
    
    def dataset_init_for_pause(self)->None:
        kwargs={'reinit_4_pause': 'reinit_4_pause'} # value is not important
        self.emit_to_dataset(**kwargs)
    
    def dataset_init_for_start(self)->None:
        kwargs={'dev_setup': self.setup} # value is not important
        self.emit_to_dataset(**kwargs)
    
    ## =========================================================================
    ##  methods for interface
    ## =========================================================================    
    def _handle_interface_error(self, error, **kwargs):
        """"""
        if isinstance(error, PortNotOpenError):
            warningMsgBox(
                "None devices available",
                f"{error.__str__()}"
            )
            
        elif isinstance(error, SerialException):
            warningMsgBox(
                "Device not detected",
                f"{error.__str__()}"

            )
        #TODO handle of the disconnection of the device
        # if (
        #     self.device._not_connected()
        #     and self.device.status_prompt != self.lab_device_status.text()
        # ):
        #     self.update_gui(DevStatus(self.device.connected(), self.device.status_prompt))
        #     errorMsgBox(
        #         "Error: Device disconnected",
        #         "The device has been disconnected!"
        #     )
        #     self._refresh_device_list()
        # elif isinstance(error, OSError):
        #     pass


    @property
    def is_connected(self)->bool:
        return self.serial_interface.is_connected.is_set()

    @property
    def connect_prompt(self)->bool:
        return f'{self.device_name} - CONNECTED'

    ## =========================================================================
    ##  
    ## =========================================================================
    #     
    @property
    def is_measuring(self)->bool:
        """"""
        return self.meas_status.is_set(MeasuringStates.MEASURING)
    @property
    def is_paused(self)->bool:
        """"""
        return self.meas_status.is_set(MeasuringStates.PAUSED)
    @property
    def is_idle(self)->bool:
        """"""
        return self.meas_status.is_set(MeasuringStates.IDLE)

    ## =========================================================================
    ##  Methods on Comunicator
    ## =========================================================================    
    
    def send_communicator(self, cmd: SciospecCmd, op: SciospecOption) -> bool:
        """"""
        data= self.setup.get_data(cmd, op)
        return self.communicator.send_cmd_frame(self.serial_interface, cmd, op, data)
    
    # def listen_activate(self, activate:bool=True):
    #     """"""

    def check_nb_meas_reached(self, idx:int,**kwargs) -> None:
        """Check if the number of Burst(measurements) is reached,
        in that case the measurement mode will be stopped on the device
        
        should be Triggered from meas_dataset"""

        if not self.is_measuring:
            return
        burst = self.setup.get_burst()
        if burst > 0 and idx == burst:
            self.stop_meas()

    def check_not_measuring(force_stop: bool = False):
        """Decorator: which check if the device is not measuring

        - if device is not measuring >> the function is run
        - if device is measuring:
            - the measurement can be stopped by setting force_stop to `True`. A 
            info msgBox will be popped to inform the user and the 
            function will be run
            - otherwise a info msgBox will be popped to ask the user to stop
            the measuremnet before using that function

        Args:
            force_stop (bool, optional): set to `True` to force the device to 
            run the function after stoping the measurements. Defaults to `False`.
        """
        
        def _check_not_measuring(func):
            
            def wrap(self, *args, **kwargs) -> Union[Any, None]:

                if args:
                    logger.debug(f"check_not_measuring :{args=}")
                elif kwargs:
                    logger.debug(f"check_not_measuring :{kwargs=}")
                else:
                    logger.debug("check_not_measuring not args kwargs")
                
                msg=None
                run_func = False
                if not self.is_measuring: # if not measuring >> run func
                    run_func= True
                elif force_stop:
                    self.stop_meas()
                    msg= "Measurements have been stopped"
                    run_func= True
                else:
                    msg="Please stop measurements first"

                if msg: # show msg only if msg is not empty/None
                    infoMsgBox("Measurements still running!",msg)

                return func(self, *args, **kwargs) if run_func else None
               
            return wrap
        return _check_not_measuring
    ## =========================================================================
    ##  Connection with device
    ## =========================================================================
    
    @check_not_measuring()
    def set_device_name(self, name:str= None,*args, **kwargs):
        if (port:= self._get_sciospec_port(name)) is None:
            logger.info(f"Sciospec device: {name} - NOT DETECTED")
        self.device_name = name if port else NONE_DEVICE

    @check_not_measuring()
    def get_devices(self,*args, **kwargs)->dict:
        """Lists the available Sciospec device is available
        - Device infos are ask and if an ack is get: it is a Sciospec device..."""
        ports = self.serial_interface.get_ports_available()
        self.sciospec_devices = {}
        for port in ports:
            device_name = self._check_is_sciospec_dev(port)
            if device_name is not None:
                self.sciospec_devices[device_name] = port
                self.device_name= device_name
        self.emit_to_gui(DevAvailables(self.sciospec_devices))
        logger.info(f"Sciospec devices available: {list(self.sciospec_devices)}")
        return self.sciospec_devices

    @check_not_measuring()
    def connect_device(self, *args, **kwargs) -> bool:
        """Connect a sciopec device"""
        
        # (port:= self._get_sciospec_port(device_name))
        if (port:= self._get_sciospec_port(self.device_name)) is None:
            return False

        if (success:=self._connect_interface(port)):        
            # self.stop_meas()# in case that the device is still measuring!
            self.get_device_infos()

        self.device_name = self.device_name if success else NONE_DEVICE
        self.emit_dev_status()
        logger.info(f"Connecting device '{self.device_name}' - {SUCCESS[success]}")
        return success

    @check_not_measuring()
    def disconnect_device(self, *args, **kwargs) -> None:
        """ Disconnect device"""
        if not self.is_connected:
            return

        if (success:=self._disconnect_interface()):        
            # Some reinitializsation of internal objects after disconnection
            self.setup.reinit()
            self.serial_interface.reinit()

        logger.info(f"Disconnecting device '{self.device_name}' - {SUCCESS[success]}")
        self.device_name = NONE_DEVICE if success else self.device_name
        self.emit_dev_status()
        self.get_devices()  # update the list of Sciospec devices available ????

    ## -------------------------------------------------------------------------
    ##  Internal methods
    ## -------------------------------------------------------------------------

    def _connect_interface(self, port: str= None, baudrate:int=None) -> bool:
        """Connect interface to port"""
        return self.serial_interface.open(port, baudrate)
            
    def _disconnect_interface(self) -> bool:
        """ " Disconnect actual interface"""
        return self.serial_interface.close()

    
    def _check_is_sciospec_dev(self, port) -> Union[str, None]:
        """Return a device name if the device presents on the port is a 
        sciospec device otherwise return `None`"""
        tmp_sn= self.setup.get_sn(in_bytes=True)
        device_name =  None
        self._connect_interface(port)
        self._stop_meas()# in case that the device is still measuring!
        self.get_device_infos()
        device_name = self.setup.build_sciospec_device_name(port)
        self._disconnect_interface()
        self.setup.set_sn(tmp_sn)
        return device_name
    
    def _get_sciospec_port(self, device_name: str)-> Union[str, None]:

        if not self.sciospec_devices:
            logger.warning('No Sciospec devices - DETECTED')
            warningMsgBox(
                'No Sciospec devices - DETECTED',
                "Please refresh the list of availables device first and retry!",
            )
            return None
        if device_name not in self.sciospec_devices:
            logger.error(f'Sciospec device "{device_name}" - NOT FOUND')
            errorMsgBox(
                "Sciospec device - NOT FOUND ",
                f'Please reconnect your device "{device_name}"',
            )
            return None
        return self.sciospec_devices[device_name]


    ## =========================================================================
    ##  Measurements with device
    ## =========================================================================
    
    def start_paused_resume_meas(self, *args, **kwargs)->bool:
        """"""
        if self.meas_status.is_set(MeasuringStates.IDLE):
            self.dataset_init_for_start()
            self.start_meas()
        elif self.meas_status.is_set(MeasuringStates.MEASURING):
            self.pause_meas()
        elif self.meas_status.is_set(MeasuringStates.PAUSED):
            self.resume_meas()

    def stop_meas(self, *args, **kwargs)-> None:
        """Stop measurements"""
        if success:= self._stop_meas():
            self.meas_status.change_state(MeasuringStates.IDLE)
            self.emit_to_gui(FrameProgress( 0, 0))
        logger.info(f"Stop Measurements - {SUCCESS[success]}")

    ## -------------------------------------------------------------------------
    ##  Internal methods
    ## -------------------------------------------------------------------------

    @check_not_measuring()
    def start_meas(self) -> bool:  # sourcery skip: class-extract-method
        """Start measurements"""
        if success:= self._start_meas():
            self.meas_status.change_state(MeasuringStates.MEASURING)
            self.emit_to_gui(FrameInfo(""))
        logger.info(f"Start Measurements - {SUCCESS[success]}")
        return success

    @check_not_measuring()
    def resume_meas(self) -> bool:
        """resume measurements"""
        if success:= self._start_meas():
            self.meas_status.change_state(MeasuringStates.MEASURING)
        logger.info(f"Resume Measurements - {SUCCESS[success]}")
        return success

    def pause_meas(self)-> None:
        """Pause measurements"""
        if success:= self._stop_meas():
            self.meas_status.change_state(MeasuringStates.PAUSED)
            self.dataset_init_for_pause()
            self.emit_to_gui(FrameProgress( None, 0)) # not update idx_frame
        logger.info(f"Pause Measurements - {SUCCESS[success]}")

        
    def _start_meas(self) -> bool:  # sourcery skip: class-extract-method
        """Start measurements"""
        success = self.send_communicator(CMD_START_STOP_MEAS, OP_START_MEAS)
        self.communicator.wait_not_busy()
        return success

    def _stop_meas(self) -> bool:
        """Stop measurements"""
        success = self.send_communicator(CMD_START_STOP_MEAS, OP_STOP_MEAS)
        self.communicator.wait_not_busy()
        return success

    ## =========================================================================
    ##  Setup device
    ## =========================================================================
    
    @check_not_measuring()
    def get_device_infos(self, *args, **kwargs)-> None:
        """Ask for the serial nummer of the Device"""
        self.send_communicator(CMD_GET_DEVICE_INFOS, OP_NULL)
        self.communicator.wait_not_busy()
        self.emit_to_gui(DevSetup(self.setup))
        logger.debug(f'Get Info Device: {self.setup.device_infos.get_sn()}')

    @check_not_measuring()
    def set_setup(self , *args, **kwargs)-> None:
        """Send the setup to the device"""
        logger.info("Setting device setup - start...")
        self.send_communicator(CMD_SET_OUTPUT_CONFIG, OP_EXC_STAMP)
        self.send_communicator(CMD_SET_OUTPUT_CONFIG, OP_CURRENT_STAMP)
        self.send_communicator(CMD_SET_OUTPUT_CONFIG, OP_TIME_STAMP)
        self.send_communicator(CMD_SET_ETHERNET_CONFIG, OP_DHCP)
        self.send_communicator(CMD_SET_MEAS_SETUP, OP_RESET_SETUP)
        self.send_communicator(CMD_SET_MEAS_SETUP, OP_EXC_AMPLITUDE)
        self.send_communicator(CMD_SET_MEAS_SETUP, OP_BURST_COUNT)
        self.send_communicator(CMD_SET_MEAS_SETUP, OP_FRAME_RATE)
        self.send_communicator(CMD_SET_MEAS_SETUP, OP_EXC_FREQUENCIES)
        for idx in range(len(self.setup.get_exc_pattern())):
            self.setup.set_exc_pattern_idx(idx)
            self.send_communicator(CMD_SET_MEAS_SETUP, OP_EXC_PATTERN)
        self.communicator.wait_not_busy()
        self.get_setup()
        logger.info("Setting device setup - done")

    @check_not_measuring()
    def get_setup(self, *args, **kwargs)-> None:
        """Get the setup of the device"""
        logger.info("Getting device setup - start...")
        self.send_communicator(CMD_GET_MEAS_SETUP, OP_EXC_AMPLITUDE)
        self.send_communicator(CMD_GET_MEAS_SETUP, OP_BURST_COUNT)
        self.send_communicator(CMD_GET_MEAS_SETUP, OP_FRAME_RATE)
        self.send_communicator(CMD_GET_MEAS_SETUP, OP_EXC_FREQUENCIES)
        self.send_communicator(CMD_GET_MEAS_SETUP, OP_EXC_PATTERN)
        self.send_communicator(CMD_GET_OUTPUT_CONFIG, OP_EXC_STAMP)
        self.send_communicator(CMD_GET_OUTPUT_CONFIG, OP_CURRENT_STAMP)
        self.send_communicator(CMD_GET_OUTPUT_CONFIG, OP_TIME_STAMP)
        self.send_communicator(CMD_GET_ETHERNET_CONFIG, OP_IP_ADRESS)
        self.send_communicator(CMD_GET_ETHERNET_CONFIG, OP_MAC_ADRESS)
        self.send_communicator(CMD_GET_ETHERNET_CONFIG, OP_DHCP)
        self.communicator.wait_not_busy()
        self.emit_to_gui(DevSetup(self.setup))
        logger.info("Getting device setup - done")
    
    @check_not_measuring()
    def software_reset(self, *args, **kwargs)->None:
        """Sofware reset the device
        Notes: a restart is needed after this method"""
        logger.info("Softreset of device - start...")
        self.send_communicator(CMD_SOFT_RESET, OP_NULL)
        self.communicator.wait_not_busy()
        sleep(10)
        self.disconnect_device() 
        logger.info("Softreset of device - done")
        infoMsgBox("Device reset ","Reset done")

    def save_setup(self,  *args, **kwargs)->None:
        """Save Setup
        to set dir use kwargs dir="the/path/to/save"
        """
        self.setup.save(**kwargs)

    def load_setup(self, *args,  **kwargs)->None:
        """Load Setup
        to set dir use kwargs dir="the/path/to/load"
        """
        self.setup.load(**kwargs)
        self.emit_to_gui(DevSetup(self.setup))









if __name__ == "__main__":
    import sys

    from PyQt5.QtWidgets import QApplication

    # app = QApplication(sys.argv)
    print(SUCCESS[True])
    print(SUCCESS[False])

    meas_status= MultiStatewSignal(list(MeasuringStates))
    meas_status.change_state(MeasuringStates.IDLE)

    print(meas_status.state.value)

    def print_e(**kwargs):
        print(f"{kwargs=}")

    main_log()

    dev = SciospecEITDevice()
    dev.to_dataset.connect(print_e)
    print('*+++++++++++++++++++++++++++++++++++++')
    dev.get_devices()
    print('*+++++++++++++++++++++++++++++++++++++')
    dev.connect_device()
    print('*+++++++++++++++++++++++++++++++++++++')
    dev.get_setup()
    print('*+++++++++++++++++++++++++++++++++++++')
    dev.set_setup()
    print('*+++++++++++++++++++++++++++++++++++++')
    dev.start_meas()
    print('*+++++++++++++++++++++++++++++++++++++')
    sleep(1)
    dev.pause_meas()
    print('*+++++++++++++++++++++++++++++++++++++')
    sleep(1)
    dev.resume_meas()
    print('*+++++++++++++++++++++++++++++++++++++')
    sleep(1)
    dev.stop_meas()
    print('*+++++++++++++++++++++++++++++++++++++')
    dev.disconnect_device()
    print('*+++++++++++++++++++++++++++++++++++++')
    dev.connect_device()


    