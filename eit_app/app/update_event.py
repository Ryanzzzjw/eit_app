from abc import ABC
from dataclasses import dataclass, is_dataclass
from enum import Enum, auto
from logging import getLogger
import threading
from typing import Any, Callable, List

from eit_app.app.gui import Ui_MainWindow
from eit_app.app.gui_utils import (
    change_value_withblockSignal,
    set_comboBox_items,
    set_slider,
    set_table_widget,
)
from eit_app.io.sciospec.device_setup import SciospecSetup
from eit_model.imaging_type import (
    Imaging,
    AbsoluteImaging,
    TimeDifferenceImaging,
    FrequenceDifferenceImaging,
)
from glob_utils.flags.flag import CustomFlag, MultiState

logger = getLogger(__name__)

def is_dataclass_instance(obj):
    return is_dataclass(obj) and not isinstance(obj, type)

################################################################################
# Event Dataclass use to trigger an update
################################################################################


class EventDataClass(ABC):
    """Abstract class of the dataclass defined for each update events"""


################################################################################
# Event Agent
################################################################################


class EventsAgent:
    """This agent apply update on the GUI (app) depending on the data posted """

    def __init__(self, app, events) -> None:
        self.subscribers = {}
        self.app = app
        self.events = events

    def _mk_dict(self, data:EventDataClass):
        """ Transform the event data in dictionarie and add the "app" key """
        d = data.__dict__
        d["app"] = self.app
        return d

    def post_event_data(self, data:EventDataClass):
        """Run the update event correspoding to the event data"""

        if not is_dataclass_instance(data) or not isinstance(data, EventDataClass):
            logger.error("data are not compatible for update")
            return

        logger.info(f"thread update_event {threading.get_ident()}")
        data = self._mk_dict(data)
        func = data.pop("func")
        logger.debug(f"updating {func=} with {data=}")
        self.events[func](**data)

################################################################################
# Update events Catalog
################################################################################

# cataolog of update functions event
UPDATE_EVENTS: dict[str, Callable] = {} 


def add_func_to_catalog(func:Callable):
    """Add the function to the catalog"""
    name = func.__name__
    UPDATE_EVENTS[func.__name__] = func


################################################################################
# Update Definition and assiociated dataclasses
################################################################################


# -------------------------------------------------------------------------------
## Update available devices
# -------------------------------------------------------------------------------


def update_available_devices(app: Ui_MainWindow, device: dict):
    """Refesh the list of devices in the comboBox"""
    items = list(device) or ["None device"]
    set_comboBox_items(app.cB_ports, items)


add_func_to_catalog(update_available_devices)

@dataclass
class DevAvailables(EventDataClass):
    device: dict
    func: str = update_available_devices.__name__


# -------------------------------------------------------------------------------
## Update device status
# -------------------------------------------------------------------------------


def update_device_status(app: Ui_MainWindow, connected: bool, status_prompt: str):
    """Actualize the status of the device"""
    app.lab_device_status.setText(status_prompt)
    app.lab_device_status.adjustSize
    color = "background-color: green" if connected else "background-color: red"
    app.lab_device_status.setStyleSheet(color)


add_func_to_catalog(update_device_status)


@dataclass
class DevStatus(EventDataClass):
    connected: bool
    status_prompt: str
    func: str = update_device_status.__name__


# -------------------------------------------------------------------------------
## Update device setup
# -------------------------------------------------------------------------------


def update_device_setup(
    app: Ui_MainWindow,
    setup: SciospecSetup,
    set_freq_max_enable: bool = True,
    error: bool = False,
):
    """Actualize the inputs fields for the setup of the device coresponding to it"""
    app.lE_sn.setText(setup.get_sn())
    ## Update EthernetConfig
    app.chB_dhcp.setChecked(bool(setup.get_dhcp()))
    app.lE_ip.setText(setup.get_ip())
    app.lE_mac.setText(setup.get_mac())

    ## Update OutputConfig Stamps
    app.chB_exc_stamp.setChecked(bool(setup.get_exc_stamp()))
    app.chB_current_stamp.setChecked(bool(setup.get_current_stamp()))
    app.chB_time_stamp.setChecked(bool(setup.get_time_stamp()))

    # Update Measurement Setups
    change_value_withblockSignal(app.sBd_frame_rate.setValue, setup.get_frame_rate())
    change_value_withblockSignal(
        app.sBd_max_frame_rate.setValue, setup.get_max_frame_rate()
    )
    change_value_withblockSignal(app.sB_burst.setValue, setup.get_burst())
    change_value_withblockSignal(
        app.sBd_exc_amp.setValue, setup.get_exc_amp() * 1000
    )  # from A -> mA
    change_value_withblockSignal(app.sBd_freq_min.setValue, setup.get_freq_min())
    change_value_withblockSignal(app.sBd_freq_max.setValue, setup.get_freq_max())
    change_value_withblockSignal(app.sB_freq_steps.setValue, setup.get_freq_steps())
    change_value_withblockSignal(app.cB_scale.setCurrentText, setup.get_freq_scale())

    app.sBd_freq_max.setEnabled(set_freq_max_enable)
    color = "background-color: red" if error else "background-color: white"
    app.label_maxF.setStyleSheet(color)
    app.label_minF.setStyleSheet(color)
    app.label_Steps.setStyleSheet(color)

    set_table_widget(app.tw_exc_pattern, setup.get_exc_pattern(), 0)
    update_freqs_list(app, setup.get_freqs())


add_func_to_catalog(update_device_setup)


@dataclass
class DevSetup(EventDataClass):
    setup: SciospecSetup
    set_freq_max_enable: bool = True
    error: bool = False
    func: str = update_device_setup.__name__


# -------------------------------------------------------------------------------
## Update Frequency list for the imaging inputs
# -------------------------------------------------------------------------------


def update_freqs_list(app: Ui_MainWindow, freqs: List[Any]):
    set_comboBox_items(app.cB_freq_meas_0, list(freqs))
    set_comboBox_items(app.cB_freq_meas_1, list(freqs))


# -------------------------------------------------------------------------------
## Update live measurements state
# -------------------------------------------------------------------------------


class LiveMeasState(Enum):
    Idle = auto()
    Measuring = auto()
    Paused = auto()


def update_live_status(app: Ui_MainWindow, live_meas: MultiState):
    """Update the live measurements status label and the mesurements
    start/pause/resume button"""

    if live_meas.is_set(LiveMeasState.Idle):
        app.lab_live_meas_status.setText("Idle")
        app.lab_live_meas_status.setStyleSheet("background-color: red")
        app.pB_start_meas.setText("Start")
        app.pB_start_meas.setStatusTip(
            "Start aquisition of a new measurement dataset (Ctrl + Shift +Space)"
        )
    elif live_meas.is_set(LiveMeasState.Measuring):
        app.lab_live_meas_status.setText("Measuring")
        app.lab_live_meas_status.setStyleSheet("background-color: green")
        app.meas_progress_bar.setValue(0)
        app.pB_start_meas.setText("Pause")
        app.pB_start_meas.setStatusTip(
            "Pause aquisition of measurement dataset (Ctrl + Shift +Space)"
        )
    elif live_meas.is_set(LiveMeasState.Paused):
        app.lab_live_meas_status.setText("Paused")
        app.lab_live_meas_status.setStyleSheet("background-color: yellow")
        app.pB_start_meas.setText("Resume")
        app.pB_start_meas.setStatusTip(
            "Restart aquisition of measurement dataset (Ctrl + Shift +Space)"
        )


add_func_to_catalog(update_live_status)


@dataclass
class LiveStatus(EventDataClass):
    live_meas: MultiState
    func: str = update_live_status.__name__


# -------------------------------------------------------------------------------
## Update replay status
# -------------------------------------------------------------------------------


def update_replay_status(app: Ui_MainWindow, status: CustomFlag):
    """Update the status label"""

    if status.is_set():
        app.lab_replay_status.setText("REPLAY ON")
        app.lab_replay_status.setStyleSheet("background-color: green")
    else:
        app.lab_replay_status.setText("REPLAY OFF")
        app.lab_replay_status.setStyleSheet("background-color: grey")
        # set_slider(app.slider_replay, set_pos=0)


add_func_to_catalog(update_replay_status)


@dataclass
class ReplayStatus(EventDataClass):
    status: CustomFlag
    func: str = update_replay_status.__name__


# -------------------------------------------------------------------------------
## Update imaging inputs fields
# -------------------------------------------------------------------------------


def update_imaging_inputs_fields(app: Ui_MainWindow, imaging: Imaging):
    """Activate deactive the input fileddepending on the imaging type"""

    meas_0 = {"show": True, "lab_text": "Meas. Frequency"}
    meas_1 = {"show": False, "lab_text": "Meas. Frequency"}
    ref = {"show": False, "lab_text": "Reference frame #"}
    if isinstance(imaging, AbsoluteImaging):
        pass
    elif isinstance(imaging, TimeDifferenceImaging):
        ref = {"show": True, "lab_text": "Reference frame #"}
    elif isinstance(imaging, FrequenceDifferenceImaging):
        meas_0 = {"show": True, "lab_text": "Ref. Frequence"}
        meas_1 = {"show": True, "lab_text": "Meas. Frequency"}

    app.cB_ref_frame_idx.setEnabled(ref["show"])
    app.lab_ref_frame_idx.setEnabled(ref["show"])
    app.lab_freq_meas_0.setText(ref["lab_text"])

    app.cB_freq_meas_0.setEnabled(meas_0["show"])
    app.lab_freq_meas_0.setEnabled(meas_0["show"])
    app.lab_freq_meas_0.setText(meas_0["lab_text"])

    app.cB_freq_meas_1.setEnabled(meas_1["show"])
    app.lab_freq_meas_1.setEnabled(meas_1["show"])
    app.lab_freq_meas_1.setText(meas_1["lab_text"])


add_func_to_catalog(update_imaging_inputs_fields)


@dataclass
class ImagingInputs(EventDataClass):
    imaging: Imaging
    func: str = update_imaging_inputs_fields.__name__


# -------------------------------------------------------------------------------
## Update eitdata plot options
# -------------------------------------------------------------------------------


def update_eitdata_plots_options(app: Ui_MainWindow):
    """Activate/deactivate checkbox for eitdata plots"""
    app.chB_Uplot.setEnabled(app.chB_plot_graph.isChecked())
    app.chB_diff.setEnabled(app.chB_plot_graph.isChecked())
    app.chB_y_log.setEnabled(app.chB_plot_graph.isChecked())


add_func_to_catalog(update_eitdata_plots_options)


@dataclass
class EITdataPlotOptions(EventDataClass):
    func: str = update_eitdata_plots_options.__name__


# -------------------------------------------------------------------------------
## Update frame aquisition progress
# -------------------------------------------------------------------------------


def update_progress_acquired_frame(
    app: Ui_MainWindow, idx_frame: int = 0, progression: int = 0
):
    """Update the progression bar and the idx of the aquired frame"""
    app.sB_actual_frame_cnt.setValue(idx_frame)
    app.meas_progress_bar.setValue(progression)


add_func_to_catalog(update_progress_acquired_frame)


@dataclass
class FrameProgress(EventDataClass):
    idx_frame: int = 0
    progression: int = 0
    func: str = update_progress_acquired_frame.__name__


# -------------------------------------------------------------------------------
## Update frame info text (during acquisition and replay)
# -------------------------------------------------------------------------------


def update_frame_info(app: Ui_MainWindow, info: str = ""):
    if info is not None:
        app.tE_frame_info.setText("\r\n".join(info))


add_func_to_catalog(update_frame_info)


@dataclass
class FrameInfo(EventDataClass):
    info: str = ""
    func: str = update_frame_info.__name__


# -------------------------------------------------------------------------------
## Update autosave inputs options
# -------------------------------------------------------------------------------


def update_autosave_options(app: Ui_MainWindow):
    """Activate/deactivate saving options"""
    app.lE_meas_dataset_dir.setEnabled(app.chB_dataset_autosave.isChecked())
    app.chB_dataset_save_img.setEnabled(app.chB_dataset_autosave.isChecked())
    app.chB_load_after_meas.setEnabled(app.chB_dataset_autosave.isChecked())
    app.chB_dataset_save_img.setChecked(
        app.chB_dataset_autosave.isChecked() and app.chB_dataset_save_img.isChecked()
    )

    app.chB_load_after_meas.setChecked(
        app.chB_dataset_autosave.isChecked() and app.chB_load_after_meas.isChecked()
    )


add_func_to_catalog(update_autosave_options)


@dataclass
class AutosaveOptions(EventDataClass):
    func: str = update_autosave_options.__name__


# -------------------------------------------------------------------------------
## Update live measurements state (after loading a measurement dataset)
# -------------------------------------------------------------------------------


def update_dataset_loaded(app: Ui_MainWindow, dataset_dir: str, nb_loaded_frame: int):
    """update the path of the loaded dataset and init the combosboxes and slider
    for the nb of loaded frames"""
    app.tE_load_dataset_dir.setText(dataset_dir)
    set_comboBox_items(app.cB_current_idx_frame, list(range(nb_loaded_frame)))
    set_comboBox_items(app.cB_ref_frame_idx, list(range(nb_loaded_frame)))
    set_slider(app.slider_replay, 0, 0, nb_loaded_frame - 1, 1)


add_func_to_catalog(update_dataset_loaded)


@dataclass
class MeasDatasetLoaded(EventDataClass):
    dataset_dir: str
    nb_loaded_frame: int
    func: str = update_dataset_loaded.__name__


if __name__ == "__main__":
    """"""
    a=DevAvailables('')
    print(a)
