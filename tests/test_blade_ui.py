"""Real command handlers with fake Fusion controls; no live modal automation."""
from contextlib import ExitStack
import importlib.util
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


class Control:
    def __init__(self, identifier, value=None):
        self.id, self._value = identifier, value
        self.isVisible = True
        self.text = ''
        self.listItems = []
        self.entities = []
        self.on_write = None

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self._value = value
        if self.on_write:
            self.on_write()

    @property
    def selectionCount(self):
        return len(self.entities)

    def selection(self, index):
        return NS(entity=self.entities[index])

    def addSelectionFilter(self, *args):
        pass

    def setSelectionLimits(self, *args):
        pass


class Inputs:
    def __init__(self):
        self.controls = {}

    def itemById(self, identifier):
        return self.controls[identifier]

    def add(self, identifier, value=None):
        control = self.controls[identifier] = Control(identifier, value)
        return control

    def addSelectionInput(self, identifier, *args):
        return self.add(identifier)

    def addBoolValueInput(self, identifier, label, checkbox, resource, value):
        return self.add(identifier, value)

    def addStringValueInput(self, identifier, label, value):
        return self.add(identifier, value)

    def addTextBoxCommandInput(self, identifier, label, text, *args):
        control = self.add(identifier)
        control.text = text
        return control

    def addValueInput(self, identifier, label, units, value):
        return self.add(identifier, value)

    def addDropDownCommandInput(self, identifier, *args):
        return self.add(identifier)


def isolated_addin(ui):
    adsk = ModuleType('adsk')
    adsk.core, adsk.fusion = ModuleType('adsk.core'), ModuleType('adsk.fusion')
    for name in ('CommandEventHandler', 'InputChangedEventHandler',
                 'ValidateInputsEventHandler', 'CommandCreatedEventHandler'):
        setattr(adsk.core, name, type(name, (), {}))
    adsk.core.Application = NS(get=lambda: NS(userInterface=ui))
    adsk.core.DialogResults = NS(DialogOK=1)
    adsk.core.ValueInput = NS(createByString=lambda value: float(value.split()[0]) / 10)
    adsk.core.DropDownStyles = NS(TextListDropDownStyle=1, CheckBoxDropDownStyle=2)
    adsk.fusion.BRepFace = NS(cast=lambda value: value)
    cam = ModuleType('tribu_exporter.cam_addin')
    for name in ('add_cam_option_inputs', 'available_cam_operations', 'cam_options_from_inputs',
                 'operation_label', 'operation_problem', 'operation_smoothing_info',
                 'post_operation', 'reset_cam_option_inputs'):
        setattr(cam, name, Mock())
    extract = ModuleType('tribu_exporter.fusion_extract')
    extract.extract_panel_ir, extract.make_panel_frame = Mock(), Mock()
    modules = {'adsk': adsk, 'adsk.core': adsk.core, 'adsk.fusion': adsk.fusion,
               cam.__name__: cam, extract.__name__: extract}
    path = Path(__file__).parents[1] / 'tribu_exporter' / 'addin.py'
    spec = importlib.util.spec_from_file_location('tribu_exporter._blade_ui_test', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class BladeUIStateTests(unittest.TestCase):
    def setUp(self):
        self.dialog = NS(filename='first.json', showOpen=Mock(return_value=1))
        self.module = isolated_addin(NS(createFileDialog=lambda: self.dialog))
        self.inputs = Inputs()
        event = lambda: NS(add=Mock())
        command = NS(commandInputs=self.inputs, execute=event(), inputChanged=event(),
                     validateInputs=event(), destroy=event())
        self.module.CreatedHandler().notify(NS(command=command))
        self.handler = command.inputChanged.add.call_args.args[0]
        self.state = self.handler.state
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(self.module, 'get_logger', return_value=Mock(spec=logging.Logger)))
        self.stack.enter_context(patch.object(self.module, '_base_inputs_valid', return_value=True))
        self.load = self.stack.enter_context(patch.object(self.module.BladeMachineProfile, 'load', return_value=NS(tool_id=123)))
        self.restore = self.stack.enter_context(patch.object(self.module, '_restore_settings', wraps=self.module._restore_settings))
        self.prefs = self.stack.enter_context(patch.object(self.module, '_load_preferences', return_value={'settings': {'blade_profile_path': 'saved.json'}}))
        self.extract = self.stack.enter_context(patch.object(self.module, '_extract_from_inputs', side_effect=self.extract_panel))
        self.stack.enter_context(patch.object(self.module, '_populate_profile_choices'))

    def extract_panel(self, inputs, logger):
        machine = self.module._blade_machine_from_inputs(inputs)
        inputs.itemById('blade_status').text = f'Analysis tool {machine.tool_id}; machine export blocked' if machine else 'Off'
        return None, object()

    def event(self, identifier):
        self.handler.notify(NS(input=self.inputs.itemById(identifier)))
        self.assertFalse(self.state.updating)

    def arm(self):
        self.inputs.itemById('export_blade_cuts').value = True
        self.event('export_blade_cuts')

    def assert_armed(self):
        self.assertTrue(self.inputs.itemById('export_blade_cuts').value)
        for identifier in ('blade_profile_path', 'choose_blade_profile', 'blade_status'):
            self.assertTrue(self.inputs.itemById(identifier).isVisible)

    def test_a_checkbox_shows_controls_without_restoring(self):
        self.arm()
        self.assert_armed()
        self.restore.assert_not_called()

    def test_b_picker_refreshes_explicitly_despite_suppressed_nested_event(self):
        self.arm()
        path = self.inputs.itemById('blade_profile_path')
        path.on_write = lambda: self.handler.notify(NS(input=path))
        self.extract.reset_mock()
        self.event('choose_blade_profile')
        self.assert_armed()
        self.assertEqual(path.value, 'first.json')
        self.extract.assert_called_once()
        self.load.assert_called_with('first.json')
        self.assertIn('tool 123', self.inputs.itemById('blade_status').text)
        self.restore.assert_not_called()

    def test_c_cancel_preserves_path_visibility_status_and_intent(self):
        self.arm()
        self.inputs.itemById('blade_profile_path').value = 'previous.json'
        status = self.inputs.itemById('blade_status').text
        self.dialog.showOpen.return_value = 0
        self.extract.reset_mock()
        self.event('choose_blade_profile')
        self.assert_armed()
        self.assertEqual(self.inputs.itemById('blade_profile_path').value, 'previous.json')
        self.assertEqual(self.inputs.itemById('blade_status').text, status)
        self.extract.assert_not_called()
        self.restore.assert_not_called()

    def test_d_changing_profile_by_picker_or_text_replans_without_disarming(self):
        self.arm()
        for event, path in (('choose_blade_profile', 'second.json'), ('blade_profile_path', 'manual.json')):
            self.dialog.filename = path
            if event == 'blade_profile_path':
                self.inputs.itemById(event).value = path
            self.extract.reset_mock()
            self.event(event)
            self.assert_armed()
            self.extract.assert_called_once()
            self.load.assert_called_with(path)
        self.restore.assert_not_called()

    def test_e_stock_tolerance_and_face_refresh_preserve_intent(self):
        self.arm()
        for event in ('margin', 'stock_width', 'stock_height', 'tolerance', 'inclined_faces'):
            self.extract.reset_mock()
            self.event(event)
            self.assert_armed()
            self.extract.assert_called_once()
        self.restore.assert_not_called()

    def test_f_only_actual_body_or_occurrence_switch_disarms(self):
        native = object()
        body = lambda entity, occurrence: NS(name='panel', nativeObject=entity, isValid=True,
            assemblyContext=NS(fullPathName=occurrence))
        self.state.body_entity = body(native, 'component:1')
        self.arm()
        self.inputs.itemById('side1').entities = [NS(body=body(native, 'component:1'))]
        self.event('side1')
        self.assert_armed()  # Different Python wrappers, same Fusion entity.
        self.restore.assert_not_called()
        for next_body in (body(object(), 'component:1'), body(native, 'component:2')):
            self.arm()
            self.inputs.itemById('side1').entities = [NS(body=next_body)]
            self.event('side1')
            self.assertFalse(self.inputs.itemById('export_blade_cuts').value)
            self.assertFalse(self.inputs.itemById('blade_status').isVisible)
            self.assertEqual(self.inputs.itemById('blade_profile_path').value, 'saved.json')
        self.assertEqual(self.restore.call_count, 2)

    def test_g_new_command_starts_off_and_can_restore_only_path(self):
        self.assertFalse(self.inputs.itemById('export_blade_cuts').value)
        self.module._restore_settings(self.inputs, {'settings': {'blade_profile_path': 'saved.json'}})
        self.assertFalse(self.inputs.itemById('export_blade_cuts').value)
        self.assertFalse(self.inputs.itemById('blade_status').isVisible)
        self.assertEqual(self.inputs.itemById('blade_profile_path').value, 'saved.json')

    def test_invalid_profile_keeps_intent_and_shows_error(self):
        self.arm()
        self.load.side_effect = ValueError('Invalid blade profile')
        self.event('choose_blade_profile')
        self.assert_armed()
        self.assertIsNone(self.state.panel)
        self.assertIn('Invalid blade profile', self.inputs.itemById('blade_status').text)
