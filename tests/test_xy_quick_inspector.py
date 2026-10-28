"""Qt Quick XY inspector behavior through canonical state and real input events."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from matplotlib.backend_bases import MouseEvent
from PySide6.QtCore import QCoreApplication, QObject, QPoint, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtQml import QQmlEngine
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from spectra_app.plot_presentation import PlotPresentation
from spectra_app.project_io import read_project, write_project, workspace_digest
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from spectra_app.xy_plot_controller import XYPlotController
from tests.quick_migration_case import QuickMigrationCase
from tests import formal_quick_helpers as formal
from tests.xy_helpers import select_xy_rows, set_xy_draft, xy_value, type_xy_field, xy_field, click_xy_item

from tests.xy_canvas_quick_helpers import click_named as click_xy_command

APP = QCoreApplication


class XYQuickInspectorTests(QuickMigrationCase):
    def setUp(self):
        QuickMigrationCase.setUp(self)
        self.addCleanup(lambda: self.assertEqual(self.quick_errors, []))
        self.workspace = self.controller.workspace
        book = create_workbook(self.workspace, 'Experiment')
        x = np.linspace(0., 4., 101)
        self.sheet = register_xy_sheet(self.workspace, book.workbook_id, 'Measurements',
            np.column_stack((x, np.sin(x), np.cos(x))), ['Time', 'Signal', 'Reference'])
        self.graph = create_comparison(self.workspace)
        self.curves = [add_curve(self.workspace, self.graph.comparison_id, self.sheet.sheet_id, 0, col,
                      label=f'Curve {i}') for i,col in enumerate((1,2,1))]
        self.controller.open_xy_comparison(self.graph.comparison_id)
        documents = self.controller.graph_documents
        self.page = documents.document_for(documents.registry.active_key)
        self.assertIsInstance(self.page, XYPlotController)
        self.bridge = self.page.inspector_bridge
        self.host.window.resize(1040, 850)
        formal.pump(100)

    def inspector_panel(self, section=0):
        receiver = self.host._floating_properties if self.controller.propertiesFloating else self.controller
        click_xy_item(self.controller, formal.item(receiver, 'task_' + str(section)))
        formal.pump()
        return self.bridge._panels[section][0]

    def test_production_quick_hosts_have_no_native_curve_editors(self):
        self.assertIs(type(QGuiApplication.instance()), QGuiApplication)
        self.assertIs(self.controller.propertiesController, self.page)
        self.assertIs(self.bridge.page, self.page)
        self.assertIs(self.page.analysis_controller.page, self.page)
        self.assertIs(self.page.canvas_bridge.page, self.page)
        for section in (0, 1, 2):
            formal.click_item(self.controller, 'task_' + str(section))
            formal.pump()
        for section in (0, 2):
            panel = self.bridge._panels[section][0]
            self.assertIsInstance(panel, QQuickItem)
            self.assertIsNotNone(QQmlEngine.contextForObject(panel))
            self.assertEqual(panel.findChildren(QWidget), [])
        analysis = formal.item(self.controller, 'xy-analysis-panel')
        self.assertIs(analysis.property('bridge'), self.page.analysis_controller.bridge)
        self.assertEqual(analysis.findChildren(QWidget), [])
        self.assertFalse(hasattr(self.page, 'curve_list'))
        self.assertEqual(self.host.window.findChildren(QWidget), [])
        model = self.bridge.curves
        self.assertEqual(model.rowCount(), 3)
        roles = {v.decode(): k for k,v in model.roleNames().items()}
        self.assertEqual(model.data(model.index(0), roles['caption']), 'Curve 0')
        self.assertIn('Measurements', model.data(model.index(0), roles['sourceDescription']))
        self.curves[0].color = '#123456'
        self.assertEqual(model.data(model.index(0), roles['curveColor']), '#123456')

    def test_selection_modifiers_and_visibility_are_independent(self):
        ids = [c.curve_id for c in self.curves]
        self.bridge.selectCurve(ids[0], 0)
        self.bridge.selectCurve(ids[2], Qt.ControlModifier.value)
        self.assertEqual(self.page._selected_ids(), {ids[0],ids[2]})
        self.bridge.setVisibility(ids[1], False)
        self.assertFalse(self.curves[1].visible)
        self.assertEqual(self.page._selected_ids(), {ids[0],ids[2]})
        self.bridge.selectCurve(ids[0], Qt.ShiftModifier.value)
        self.assertEqual(self.page._selected_ids(), set(ids))
        self.assertEqual(self.page._active_curve_id, ids[0])
        self.bridge.selectAll(False)
        self.assertEqual(self.page._selected_ids(), set())
        self.assertEqual(self.page._active_curve_id, ids[0])

    def test_invalid_and_intermediate_drafts_block_switch_save_export_and_undo(self):
        before = workspace_digest(self.workspace)
        for field, value in (('color','invalid'), ('width','-'), ('stack_step','nan'), ('name','')):
            with self.subTest(field=field):
                set_xy_draft(self.page, field, value)
                self.assertFalse(select_xy_rows(self.page,[1]))
                self.assertEqual(self.page._active_curve_id,self.curves[0].curve_id)
                with self.assertRaises(ValueError): self.page.finish_editor()
                with self.assertRaises(ValueError): self.page.svg_bytes()
                self.assertFalse(self.page.undo())
                self.assertEqual(xy_value(self.page, field), value)
                self.page.revert_editor()
        self.assertEqual(workspace_digest(self.workspace),before)
        self.assertFalse(self.page.can_undo())

    def test_current_curve_drafts_commit_to_original_identity_before_switch(self):
        for key,value in (('label','Pending label'),('color','#abcdef'),('width','3.125'),('y_offset','7.25')):
            set_xy_draft(self.page,key,value)
        self.page.refresh()
        self.assertEqual(self.curves[0].label,'Curve 0')
        self.assertTrue(select_xy_rows(self.page,[1]))
        self.assertEqual((self.curves[0].label,self.curves[0].width,self.curves[0].y_offset),('Pending label',3.125,7.25))
        self.assertEqual(self.curves[1].label,'Curve 1')
        self.assertEqual(xy_value(self.page,'label'),'Curve 1')
        self.assertTrue(self.page.undo())
        self.assertEqual(self.curves[0].label,'Curve 0')

    def test_ordinary_enter_and_same_panel_blur_commit_real_qml_fields(self):
        panel, field = type_xy_field(self.page,'label','Typed label')
        self.assertEqual(self.curves[0].label,'Curve 0')
        QTest.keyClick(formal.input_receiver(self.controller, field),Qt.Key_Return); APP.processEvents()
        self.assertEqual(self.curves[0].label,'Typed label')
        type_xy_field(self.page,'color','#112233')
        width = formal.ensure_visible(formal.item(self.controller,'xyField_width'))
        click_xy_item(panel,width)
        QTest.qWait(20)
        self.assertEqual(self.curves[0].color,'#112233')
        self.assertFalse(self.page.has_pending_project_edits())

    def test_real_tab_hide_disclosure_and_revert_retain_or_cancel_draft(self):
        panel,field = type_xy_field(self.page,'label','Still pending')
        formal.click_item(self.controller, 'task_2')
        self.assertEqual(self.curves[0].label,'Curve 0')
        self.assertEqual(xy_value(self.page,'label'),'Still pending')
        formal.click_item(self.controller, 'task_0')
        panel,field = type_xy_field(self.page,'label','Hidden pending')
        click_xy_command(self.controller,'XYInspectorToggle')
        self.assertEqual(self.curves[0].label,'Curve 0')
        self.assertEqual(xy_value(self.page,'label'),'Hidden pending')
        click_xy_command(self.controller,'XYInspectorToggle')
        type_xy_field(self.page,'label','Disclosure draft')
        disclosure = formal.item(self.controller,'xySymbolsDisclosure')
        click_xy_item(panel,disclosure); QTest.qWait(20)
        self.assertEqual(self.curves[0].label,'Curve 0')
        self.assertEqual(xy_value(self.page,'label'),'Disclosure draft')
        self.bridge.revert()
        self.assertFalse(self.page.has_pending_project_edits())
        self.assertEqual(self.curves[0].label,'Curve 0')

    def test_explicit_symbol_apply_selected_scope_and_undo(self):
        select_xy_rows(self.page,[0,2],current=1)
        self.bridge.setSymbolTarget('selected')
        self.bridge.setDraft('marker_shape','s')
        self.bridge.setDraft('marker_fill','left')
        self.bridge.setDraft('marker_size','8.125')
        self.bridge.setDraft('marker_edge_color','#123456')
        self.bridge.setDraft('marker_edge_width','2.25')
        self.assertEqual(self.curves[0].marker_size,5.)
        self.bridge.commitField('label')
        self.assertEqual(self.curves[0].marker_size,5.)
        self.bridge.action('symbols')
        self.assertEqual([c.marker_size for c in self.curves],[8.125,5.,8.125])
        self.assertEqual(self.curves[0].marker_fill,'left')
        self.assertEqual(self.curves[0].marker_edge_color,'#123456')
        self.assertTrue(self.page.undo())
        self.assertEqual([c.marker_size for c in self.curves],[5.,5.,5.])

    def test_palette_order_reorder_visibility_and_structural_history(self):
        ids=[c.curve_id for c in self.curves]
        select_xy_rows(self.page,[0,2])
        self.assertTrue(self.page._move_curves({ids[2]},0))
        self.assertEqual(list(self.graph.curves),[ids[2],ids[0],ids[1]])
        with TemporaryDirectory() as directory:
            path=Path(directory)/'palette.txt'; path.write_text('#123456\n#abcdef',encoding='utf-8')
            self.assertTrue(self.page.import_palette(path))
            self.bridge.action('palette')
            self.assertEqual((self.curves[2].color,self.curves[0].color),('#123456','#abcdef'))
            path.write_text('#123456\ninvalid',encoding='utf-8')
            count=len(self.bridge.palettes)
            self.assertFalse(self.page.import_palette(path))
            self.assertEqual(len(self.bridge.palettes),count)
        self.bridge.visibilityCommand('only')
        self.assertEqual([c.visible for c in self.curves],[True,False,True])
        current=self.page._active_curve_id
        self.bridge.action('remove')
        self.assertNotIn(current,self.graph.curves)
        self.assertTrue(self.page.undo())
        self.assertIs(self.graph.curves[current],self.curves[0])
        self.assertEqual(self.page._active_curve_id,current)
        self.assertTrue(self.page.redo())
        self.assertNotIn(current,self.graph.curves)

    def test_normalization_interval_stack_reset_and_full_resolution_analysis(self):
        self.bridge.setDraft('normalization','maxabs')
        self.bridge.setRangeMode('manual')
        self.bridge.setDraft('x_min','1.25'); self.bridge.setDraft('x_max','3.25')
        self.bridge.setDraft('stack_step','2.5'); self.page.finish_editor()
        self.assertEqual((self.graph.normalization,self.graph.x_range,self.graph.stack_step),('maxabs',(1.25,3.25),2.5))
        display=self.page.axes._comparison_display
        valid = np.isfinite(display.curves[1].y)
        x = display.curves[1].x[valid]
        np.testing.assert_allclose(display.curves[1].y[valid], np.cos(x)/np.max(np.abs(np.cos(x))) + 2.5)
        self.bridge.action('reset')
        self.assertEqual((self.graph.normalization,self.graph.x_range,self.graph.stack_step),('none',None,0.))
        self.assertTrue(self.page.undo())
        self.assertEqual(self.graph.x_range,(1.25,3.25))

    def test_imported_precision_untouched_save_export_and_format_edit_undo(self):
        from dataclasses import replace
        self.curves[0].width=.123456789012345
        p=replace(self.graph.presentation,width_in=3.6023622047244097,height_in=3.4511811023622047,
                  numeric_pt=9.123456789012345,frame_width_in=2.512345678901234)
        self.graph.presentation=p
        self.page.refresh(); self.page.finish_editor(); self.page.svg_bytes()
        self.assertIs(self.graph.presentation,p)
        self.assertEqual(self.curves[0].width,.123456789012345)
        self.assertFalse(self.page.has_pending_project_edits())
        set_xy_draft(self.page,'width_mm','120',commit=True)
        self.assertAlmostEqual(self.graph.presentation.width_in,120/25.4)
        self.assertEqual(self.graph.presentation.numeric_pt,p.numeric_pt)
        self.assertTrue(self.page.undo()); self.assertEqual(self.graph.presentation,p)
        set_xy_draft(self.page,'label','Saved pending')
        self.page.finish_editor()
        target=self.folder/'inspector.spectra'; write_project(target,self.workspace)
        restored=read_project(target).comparisons[self.graph.comparison_id]
        self.assertEqual(next(iter(restored.curves.values())).label,'Saved pending')
        self.assertEqual(restored.presentation,p)

    def test_draft_typing_selection_and_style_do_not_prepare_scientific_data(self):
        with patch('spectra_app.xy_plot_controller.prepare_comparison',side_effect=AssertionError('expensive science')):
            with patch.object(self.page,'render',wraps=self.page.render) as render:
                type_xy_field(self.page,'label','Draft only')
                self.assertEqual(render.call_count,0)
                self.page.finish_editor()
                self.bridge.selectCurve(self.curves[1].curve_id,0)
                self.bridge.applyStyle('scatter')
                self.bridge.setHighlight(False)
                self.assertEqual(render.call_count,2)
                self.assertTrue(all(call.kwargs.get('appearance_only') for call in render.call_args_list))

    def test_plot_pick_and_real_qml_row_mouse_keyboard(self):
        panel=self.inspector_panel()
        def descendants(item):
            for child in item.childItems():
                yield child
                yield from descendants(child)
        rows=[obj for obj in descendants(panel) if obj.property('rowId')]
        self.assertEqual(len(rows),3)
        row=next(row for row in rows if row.property('rowId')==self.curves[1].curve_id)
        pos=row.mapToScene(QPointF(row.width()/2,row.height()/2))
        QTest.mouseClick(self.host.window,Qt.LeftButton,pos=QPoint(round(pos.x()),round(pos.y())))
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(),{self.curves[1].curve_id})
        QTest.keyClick(self.host.window,Qt.Key_Down); APP.processEvents()
        self.assertEqual(self.page._selected_ids(),{self.curves[2].curve_id})
        self.page.canvas.draw()
        line=self.page.axes.lines[0]
        x,y=line.get_transform().transform(line.get_xydata()[20])
        self.page._plot_clicked(MouseEvent('button_press_event',self.page.canvas,x,y,button=1))
        self.assertEqual(self.page._selected_ids(),{self.curves[0].curve_id})




    def test_removed_document_bindings_are_empty_disabled_and_never_mutate_stale_owner(self):
        errors=[]
        owner=self.workspace.comparisons.pop(self.graph.comparison_id)
        self.addCleanup(self.workspace.comparisons.__setitem__, self.graph.comparison_id, owner)
        with patch('sys.excepthook',lambda *args: errors.append(str(args[1]))):
            self.bridge.changed.emit(); APP.processEvents()
            self.assertFalse(self.bridge.enabled)
            self.assertEqual(self.bridge.graphName,'')
            self.assertEqual(self.bridge.error,'')
            self.assertFalse(self.bridge.hasCurrent)
            self.bridge.setDraft('label','Stale mutation')
            self.bridge.action('remove')
            self.bridge.setRangeMode('manual')
            self.assertEqual(self.curves[0].label,'Curve 0')
            self.assertEqual(len(owner.curves),3)
            self.assertEqual(errors,[])

    def test_palette_symbol_previews_and_actual_choice_keyboard(self):
        self.assertEqual(len(self.bridge.palettes[0]['swatches']),10)
        self.assertTrue(self.bridge.shapes[0]['icon'].startswith('data:image/png;base64,'))
        self.assertTrue(self.bridge.fills[0]['icon'].startswith('data:image/png;base64,'))
        panel=self.inspector_panel()
        disclosure=formal.item(self.controller,'xySymbolsDisclosure')
        disclosure.setProperty('checked',True); QTest.qWait(20)
        choice=formal.item(self.controller,'xyChoice_marker_shape')
        formal.ensure_visible(choice)
        choice.forceActiveFocus(); self.host.window.requestActivate()
        QTest.keyClick(self.host.window,Qt.Key_Down); APP.processEvents()
        self.assertEqual(xy_value(self.page,'marker_shape'),'o')
        self.assertEqual(self.curves[0].marker_shape,'ball')
        self.bridge.action('symbols')
        self.assertEqual(self.curves[0].marker_shape,'o')

    def test_reset_display_cancels_only_display_drafts_and_keeps_unrelated_invalid_text(self):
        self.graph.normalization='maxabs'; self.graph.stack_step=4.; self.curves[0].y_offset=2.
        self.page.refresh()
        set_xy_draft(self.page,'color','invalid')
        set_xy_draft(self.page,'stack_step','-')
        self.bridge.action('reset')
        self.assertEqual((self.graph.normalization,self.graph.stack_step,self.curves[0].y_offset),('none',0.,0.))
        self.assertEqual(xy_value(self.page,'color'),'invalid')
        self.assertEqual(xy_value(self.page,'stack_step'),'0.0')
        self.assertTrue(self.page.has_pending_project_edits())

    def test_hidden_invalid_field_is_revealed_focused_and_scrolled_for_save(self):
        self.bridge.setDraft('raster_dpi','-')
        formal.click_item(self.controller, 'task_0')
        click_xy_command(self.controller,'XYInspectorToggle')
        self.assertFalse(self.page.command_bridge.inspectorChecked)
        with self.assertRaises(ValueError):
            self.page.finish_editor()
        QTest.qWait(30)
        self.assertTrue(self.page.command_bridge.inspectorChecked)
        self.assertEqual(self.controller.taskIndex,2)
        item=formal.item(self.controller,'xyField_raster_dpi')
        self.assertTrue(item.isVisible())
        self.assertTrue(item.hasActiveFocus())
        viewport=item.parentItem()
        while viewport is not None and viewport.metaObject().indexOfProperty('contentY') < 0:
            viewport=viewport.parentItem()
        self.assertIsNotNone(viewport, 'Editor must belong to the real scrolling viewport')
        point=item.mapToItem(viewport,QPointF(0,0))
        self.assertGreaterEqual(point.y(),0)
        self.assertLessEqual(point.y()+item.height(),viewport.height())
        self.assertGreaterEqual(point.x(),0)
        self.assertLessEqual(point.x()+item.width(),viewport.width())
        self.assertEqual(xy_value(self.page,'raster_dpi'),'-')

    def test_real_qml_multirow_drag_keeps_selected_identity_and_history(self):
        panel=self.inspector_panel()
        select_xy_rows(self.page,[0,1])
        curve_list=formal.item(self.controller,'xyCurveList')
        start=curve_list.mapToScene(QPointF(curve_list.width()/2,21))
        stop=curve_list.mapToScene(QPointF(curve_list.width()/2,124))
        QTest.mousePress(self.host.window,Qt.LeftButton,pos=QPoint(round(start.x()),round(start.y())))
        QTest.mouseMove(self.host.window,QPoint(round(stop.x()),round(stop.y())),delay=30)
        QTest.mouseRelease(self.host.window,Qt.LeftButton,pos=QPoint(round(stop.x()),round(stop.y())))
        QTest.qWait(20)
        ids=[c.curve_id for c in self.curves]
        self.assertEqual(list(self.graph.curves),[ids[2],ids[0],ids[1]])
        self.assertEqual(self.page._selected_ids(),set(ids[:2]))
        self.assertTrue(self.page.undo())
        self.assertEqual(list(self.graph.curves),ids)

    def test_interval_navigation_preserves_draft_and_focuses_actual_qml_choice(self):
        type_xy_field(self.page,'label','Retained on interval navigation')
        self.page.edit_x_interval(); QTest.qWait(30)
        choice=formal.item(self.controller,'xyChoice_x_range_mode')
        self.assertTrue(choice.hasActiveFocus())
        self.assertEqual(xy_value(self.page,'label'),'Retained on interval navigation')
        self.assertEqual(self.curves[0].label,'Curve 0')

    def test_narrow_320_render_has_one_scroll_region_and_propagates_font(self):
        self.curves[0].label = 'Full measurement filename with a very long caption ' * 12
        self.page.refresh()
        formal.click_item(self.controller, 'floatPropertiesButton')
        window = self.host._floating_properties
        window.setWidth(320)
        font = QGuiApplication.font(); font.setFamily('Arial'); font.setPointSizeF(14.0)
        formal.pump()
        self.assertEqual(window.width(),320)
        self.assertEqual(window.contentItem().width(),320)
        for section in (0, 2):
            panel = self.inspector_panel(section)
            self.assertIsInstance(panel, QQuickItem)
            self.assertLessEqual(panel.width(),320)
            panel.setProperty('controlFont',font)
            formal.pump()
            self.assertEqual(panel.property('controlFont').pointSizeF(),14.0)
            child_field = 'label' if section == 0 else 'name'
            self.assertEqual(formal.item(panel,'xyField_'+child_field).property('font').pointSizeF(),14.0)
            scroll_regions = [child for child in panel.childItems()
                              if child.metaObject().indexOfProperty('contentItem') >= 0]
            self.assertEqual(len(scroll_regions),1)
        self.inspector_panel(0)
        curve_list = formal.item(window,'xyCurveList')
        self.assertLessEqual(curve_list.width(),window.width())
        for field in ('label','color','width','y_offset'):
            item=formal.item(window,'xyField_'+field)
            self.assertLessEqual(item.width(),window.width())
        self.assertFalse(window.grabWindow().isNull())
        self.bridge.setDraft('label','Blocked')
        scene = self.controller.graph_documents.content_item(('xy',self.graph.comparison_id,''))
        scene.setEnabled(False)
        formal.pump()
        self.bridge.action('remove'); self.bridge.setDraft('label','Should not change')
        self.assertEqual(xy_value(self.page,'label'),'Blocked')
        self.assertEqual(len(self.graph.curves),3)
