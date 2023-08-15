import bpy
from .constant import LOG, COMPATIBLE_FORMATS, SUCCESS_COLOR, CANCELLED_COLOR, SCROLL_OFFSET_INCREMENT
from bpy_extras.io_utils import ImportHelper
import os, time
from os import path
import math
from string import punctuation
from .format_handler import TILA_umi_format_handler
from .OP_command_batcher import draw_command_batcher
from .preferences import get_prefs


class TILA_umi_settings(bpy.types.Operator, ImportHelper):
	bl_idname = "import_scene.tila_universal_multi_importer_settings"
	bl_label = "Import ALL"
	bl_options = {'REGISTER', 'INTERNAL'}
	bl_region_type = "UI"

	import_format : bpy.props.StringProperty(name='Import Format', default="", options={'HIDDEN'},)
	
	
	def unregister_annotations(self):
		for a in self.registered_annotations:
			del self.__class__.__annotations__[a]

	def populate_property(self, property_name, property_value):
		self.__class__.__annotations__[property_name] = property_value

	def execute(self, context):
		self.umi_settings = context.scene.umi_settings
		# set the scene setting equal to the setting set by the user
		for k,v in self.__class__.__annotations__.items():
			if getattr(v, 'is_hidden', False) or getattr(v, 'is_readonly', False):
				continue
			if k in dir(self.format_handler.format_settings):
				try:
					setattr(self.format_handler.format_settings, k, getattr(self, k))
				except AttributeError as e:
					LOG.error("{}".format(e))

		if self.umi_settings.umi_last_setting_to_get:
			self.umi_settings.umi_ready_to_import = True

		self.umi_settings.umi_current_format_setting_imported = True
		self.unregister_annotations()

		return {'FINISHED'}

	def invoke(self, context, event):
		key_to_delete = []
		self.registered_annotations = []
		self.format_handler = eval('TILA_umi_format_handler(import_format="{}", context=cont)'.format(self.import_format), {'self':self, 'TILA_umi_format_handler':TILA_umi_format_handler, 'cont':context})

		for k,v in self.format_handler.format_annotations.items():
			if getattr(v, 'is_hidden', False) or getattr(v, 'is_readonly', False):
				key_to_delete.append(k)
			
			if k in self.format_handler.format['ignore']:
				continue

			if self.format_handler.format_is_imported: 
				if k in dir(self.format_handler.import_setting):
					value = getattr(self.format_handler.import_setting, k)
					self.populate_property(k, value)
				self.format_handler.format_settings.__annotations__[k] = value
				self.registered_annotations.append(k)
			else:
				self.populate_property(k, v)
				self.format_handler.format_settings.__annotations__[k] = v
				self.registered_annotations.append(k)

		for k in key_to_delete:
			del self.format_handler.format_annotations[k]

		bpy.utils.unregister_class(TILA_umi_settings)
		bpy.utils.register_class(TILA_umi_settings)

		wm = context.window_manager
		if len(self.format_handler.format_annotations)-2 > 0 :
			return wm.invoke_props_dialog(self)
		else:
			return self.execute(context)

	def draw(self, context):
		layout = self.layout
		col = layout.column()
		if len(self.format_handler.format_annotations):
			col.label(text='{} Import Settings'.format(self.format_handler.format_name))
			col.separator()
			for k in self.__class__.__annotations__.keys():
				if not k in ['name', 'settings_imported', 'import_format']:
					col.prop(self, k)


class TILA_umi(bpy.types.Operator, ImportHelper):
	bl_idname = "import_scene.tila_universal_multi_importer"
	bl_label = "Import ALL"
	bl_options = {'REGISTER', 'INTERNAL'}
	bl_region_type = "UI"

	# Selected files
	files : bpy.props.CollectionProperty(type=bpy.types.PropertyGroup)
	import_simultaneously_count : bpy.props.IntProperty(name="Import Simultaneously (files)", default=20, min=1, description='Maximum number of file to import simultaneously')
	max_batch_size : bpy.props.FloatProperty(name="Max batch size (MB)", description="Max size per import batch. An import batch represents the number of files imported simultaneously", default=15, min=0)
	create_collection_per_file : bpy.props.BoolProperty(name='Create collection per file', description='Each imported file will be placed in a collection', default=False)
	backup_file_after_import : bpy.props.BoolProperty(name='Backup file', description='Backup file after importing file. The frequency will be made based on "Bakup Step Parameter"',  default=False)
	backup_step : bpy.props.IntProperty(name='Backup Step (file)', description='Backup file after X file(s) imported', default=1, min=1, soft_max=50)
	skip_already_imported_files : bpy.props.BoolProperty(name='Skip already imported files', description='Import will be skipped if a Collection with the same name is found in the Blend file. "Create collection per file" need to be enabled', default=False)
	save_file_after_import : bpy.props.BoolProperty(name='Save file after import', description='Save the original file when the entire import process is compete', default=False)
	ignore_post_process_errors : bpy.props.BoolProperty(name='Ignore Post Process Errors', description='If any error occurs during post processing of imported file(s), error(s) will be ignore and the import process will continue to the next operation', default=True)
	import_svg_as_grease_pencil : bpy.props.BoolProperty(name='Import SVG as Grease Pencil', description='SVG file will be imported as grease Pencil instead of curve objects', default=False)

	_timer = None
	thread = None
	progress = 0
	current_files_to_import = None
	importing = False
	processing = False
	all_parameters_imported = False
	first_setting_to_import = True
	formats_to_import = []
	import_complete = False
	canceled = False
	import_complete = False
	current_backup_step = 0
	counter = 0
	counter_start_time = 0.0
	counter_end_time = 0.0
	delta = 0.0
	previous_counter = 0

	def invoke(self, context, event):
		bpy.context.scene.umi_settings.umi_batcher_is_processing = False
		bpy.ops.scene.umi_load_preset_list()
		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}

	def decrement_counter(self):
		self.counter = self.counter + (self.counter_start_time - self.counter_end_time)*1000
	
	def store_delta_start(self):
		self.counter_start_time = time.perf_counter()

	def store_delta_end(self):
		self.counter_end_time = time.perf_counter()
	
	def log_end_text(self):
		LOG.info('-----------------------------------')
		if self.import_complete:
			LOG.info('Batch Import completed successfully !', color=SUCCESS_COLOR)
			LOG.esc_message = '[Esc] to Hide'
			LOG.message_offset = 4
		else:
			LOG.info('Batch Import cancelled !', color=CANCELLED_COLOR)
			
		LOG.info('Click [ESC] to hide this text ...')
		LOG.info('-----------------------------------')
		self.end_text_written = True
		bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

	def draw(self, context):
		layout = self.layout
		col = layout.column()
		col.label(text='Import Settings')
		
		import_count = col.box()
		import_count.label(text='File Count')
		import_count.prop(self, 'import_simultaneously_count')
		import_count.prop(self, 'max_batch_size')

		settings = col.box()
		settings.label(text='Settings')
		settings.prop(self, 'ignore_post_process_errors')
		settings.prop(self, 'save_file_after_import')
		# settings.prop(self, 'import_svg_as_grease_pencil')
		settings.prop(self, 'create_collection_per_file')
		
		if self.create_collection_per_file:
			row = settings.row()
			split = row.split(factor=0.1, align=True)
			split.label(text='')
			split = split.split()
			split.prop(self, 'skip_already_imported_files')


		backup = col.box()
		backup.label(text='Backup')
		backup.prop(self, 'backup_file_after_import')

		if self.backup_file_after_import:
			row = backup.row()
			split = row.split(factor=0.1, align=True)
			split.label(text='')
			split = split.split()
			split.prop(self, 'backup_step')
			
		
		draw_command_batcher(self, context)

	def recur_layer_collection(self, layer_coll, coll_name):
		found = None
		if (layer_coll.name == coll_name):
			return layer_coll
		for layer in layer_coll.children:
			found = self.recur_layer_collection(layer, coll_name)
			if found:
				return found

	def post_import_command(self, objects, operator_list):
		bpy.ops.object.select_all(action='DESELECT')
		for o in objects:
			bpy.data.objects[o.name].select_set(True)
		bpy.ops.object.tila_umi_command_batcher('INVOKE_DEFAULT', operator_list=operator_list, importer_mode=True)

	def import_settings(self):
		self.current_format = self.formats_to_import.pop()

		if len(self.formats_to_import) == 0:
			self.umi_settings.umi_last_setting_to_get = True
		
		self.umi_settings.umi_current_format_setting_imported = False

		# gather import setting from the user for each format selected
		bpy.ops.import_scene.tila_universal_multi_importer_settings('INVOKE_DEFAULT', import_format=self.current_format['name'])
		self.first_setting_to_import = False

	def finish(self, context, canceled=False):
		bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
		self.revert_parameters(context)
		bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
		if canceled:
			return {'CANCELLED'}
		else:
			return {'FINISHED'}

	def modal(self, context, event):
		if not self.import_complete and event.type in {'ESC'} and event.value == 'PRESS':
			LOG.error('Cancelling...')
			self.cancel(context)

			self.log_end_text()
			self.counter = self.wait_before_hiding
			self.import_complete = True
			LOG.completed = True
			self.umi_settings.umi_import_settings.umi_import_cancelled = True
			return {'RUNNING_MODAL'}
		
		if self.import_complete:
			if not self.show_scroll_text:
				bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
				self.show_scroll_text = True
			if event.type in {'WHEELUPMOUSE'} and event.ctrl:
				LOG.scroll_offset -= SCROLL_OFFSET_INCREMENT
				bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
				return {'PASS_THROUGH'}
			elif event.type in {'WHEELDOWNMOUSE'} and event.ctrl:
				LOG.scroll_offset += SCROLL_OFFSET_INCREMENT
				bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
				return {'PASS_THROUGH'}
			
			if self.auto_hide_text_when_finished:
				self.store_delta_start()

				if self.counter == self.wait_before_hiding:
					self.previous_counter = self.counter
					self.store_delta_end()
					
				remaining_seconds = math.ceil(self.counter)

				if remaining_seconds < self.previous_counter:
					LOG.info(f'Hidding in {remaining_seconds}s ...')
					self.previous_counter = remaining_seconds

				if self.counter <= 0:
					return self.finish(context, self.canceled)
			
			if event.type in {'ESC'} and event.value == 'PRESS':
				return self.finish(context, self.canceled)
			
			if self.auto_hide_text_when_finished:
				self.previous_counter = remaining_seconds
				self.store_delta_end()
				self.decrement_counter()
				bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

			return {'RUNNING_MODAL'}
		
		if event.type == 'TIMER':
			# Loop through all import format settings
			if not self.umi_settings.umi_ready_to_import:
				if not self.first_setting_to_import:
					if not self.umi_settings.umi_current_format_setting_imported:
						return {'PASS_THROUGH'}
					else:
						self.import_settings()
				else:
					self.import_settings()
			
			# Import Loop
			else:
				if self.start_time == 0: #INIT Counter
					self.start_time = time.perf_counter()

				if bpy.context.scene.umi_settings.umi_batcher_is_processing: # wait if post processing in progress
					return {'PASS_THROUGH'}
				
				elif not len(self.objects_to_process) and not self.importing and self.current_object_to_process is None and self.current_file_number and self.current_files_to_import == []: # Import and Processing done
					i=0
					for name in self.current_filenames:
						message = f'File {i + 1} is imported successfully : {name}'
						LOG.success(message)
						LOG.store_success(message)
						i += 1

					if self.backup_file_after_import:
						if self.backup_step <= self.current_backup_step:
							self.current_backup_step = 0
							LOG.info('Saving backup file : {}'.format(path.basename(self.blend_backup_file)))
							bpy.ops.wm.save_as_mainfile(filepath=self.blend_backup_file, check_existing=False, copy=True)

					self.current_files_to_import = []

					if len(self.filepaths):
						LOG.separator()
						self.next_file()
						LOG.info(f'Current batch size : {round(self.current_batch_size, 2)}MB')
					else:
						if self.save_file_after_import:
							bpy.ops.wm.save_as_mainfile(filepath=self.current_blend_file, check_existing=False)

						LOG.complete_progress_importer(show_successes=False, duration=round(time.perf_counter() - self.start_time, 2), size=self.total_imported_size)
						self.import_complete = True
						LOG.completed = True
						self.log_end_text()
						self.counter = self.wait_before_hiding
						bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

				elif len(self.objects_to_process): # Processing current object
					self.post_import_command(self.objects_to_process, self.operator_list)
					self.objects_to_process = []
				
				elif self.importing and self.import_succedeed: # Post Import Processing 
					if len(self.operator_list):
						self.processing = True
					self.importing = False

				elif self.current_files_to_import == [] and len(self.filepaths):
					self.next_file()
					LOG.info(f'Current batch size : {round(self.current_batch_size, 2)}MB')

				elif not self.importing and len(self.current_files_to_import): # Import can start
					self.import_succedeed = self.import_files(context, self.current_files_to_import)
					self.current_files_to_import = []

				elif self.current_files_to_import == [] and len(self.filepaths):
					self.importing = False

		return {'PASS_THROUGH'}
	
	# from : https://www.digitalocean.com/community/tutorials/how-to-get-file-size-in-python
	def get_filesize(self, file_path):
		file_stats = os.stat(file_path)

		# print(file_path)
		# print(f'File Size in Bytes is {file_path.st_size}')
		filesize = file_stats.st_size / (1024 * 1024)
		# print(f'File Size in MegaBytes is {filesize}')
		return filesize

	def import_command(self, context, filepath,):
		ext = os.path.splitext(filepath)[1]
		operators = COMPATIBLE_FORMATS.get_operator_name_from_extension(ext)
		format_names = COMPATIBLE_FORMATS.get_format_from_extension(ext)['name']
		if operators is None:
			message = f"'{ext}' format is not supported"
			LOG.error(message)
			LOG.store_failure(message)
			return

		if len(operators) == 1:
			operators = operators['default']
		else:
			if ext == '.svg':
				if self.import_svg_as_grease_pencil:
					operators = operators['grease_pencil']
				else:
					operators = operators['default']

		# Double \\ in the path causing error in the string
		args = eval(f'self.{format_names}_format.format_settings_dict', {'self':self})
		raw_path = filepath.replace('\\\\', punctuation[23])
		args['filepath'] = 'r"{}"'.format(raw_path)

		args_as_string = ''
		arg_number = len(args.keys())
		for k,v in args.items():
			if k in ['settings_imported', 'name']:
				arg_number -= 1
				continue
			args_as_string += ' {}={}'.format(k, v)
			if arg_number >= 2:
				args_as_string += ','

			arg_number -= 1
			
		command = '{}({})'.format(operators, args_as_string)
		# Execute the import command
		try:
			exec(command, {'bpy':bpy})
		except Exception as e:
			LOG.error(str(e))
			LOG.store_failure(str(e))
			return False
			# raise Exception(e)

		if len(self.operator_list):
			self.objects_to_process = self.objects_to_process + [o for o in context.selected_objects]
		return True

	def update_progress(self):
			self.progress += 100/self.number_of_operations
		
	def import_files(self, context, filepaths):
		self.importing = True
		success = True
		i = 0
		for f in filepaths:
			name = path.basename(path.splitext(f)[0])
			name = path.basename(name)
			self.current_filenames.append(path.basename(name))

			if self.skip_already_imported_files:
				if name in bpy.data.collections:
					self.current_files_to_import = []
					self.importing = False
					LOG.warning('File {} have already been imported, skiping file...'.format(name))
					return
			
			
			self.update_progress()
			LOG.info('Importing file {}/{} - {}% : {}'.format(i + 1 , self.number_of_files, round(self.progress,2), name), color=(0.13, 0.69, 0.72))
			self.current_backup_step += 1

			if self.create_collection_per_file:
				collection = bpy.data.collections.new(name=name)
				self.root_collection.children.link(collection)
				
				root_layer_col = self.view_layer.layer_collection    
				layer_col = self.recur_layer_collection(root_layer_col, collection.name)
				self.view_layer.active_layer_collection = layer_col
		
			success = success and self.import_command(context, filepath=f)
			i += 1
		return success

	def get_compatible_extensions(self):
		return COMPATIBLE_FORMATS.extensions

	def store_formats_to_import(self):
		for f in self.filepaths:
			format = COMPATIBLE_FORMATS.get_format_from_extension(path.splitext(f)[1])
			if format not in self.formats_to_import:
				self.formats_to_import.append(format)

	def revert_parameters(self, context):
		self.formats_to_import = []
		self.all_parameters_imported = False
		self.thread = None
		self.progress = 0
		self.current_backup_step = 0
		self.current_files_to_import = []
		self.importing = False
		self.first_setting_to_import = True
		self.import_complete = False
		self.canceled = False
		self.import_complete = False
		self.umi_settings.umi_last_setting_to_get = False
		self.umi_settings.umi_ready_to_import = False
		self.umi_settings.umi_current_format_setting_imported = False
		context.window_manager.event_timer_remove(self._timer)
		LOG.revert_parameters()
		LOG.clear_all()
	
	def execute(self,context):
		bpy.utils.unregister_class(TILA_umi_settings)
		bpy.utils.register_class(TILA_umi_settings)
		self.current_blend_file = bpy.data.filepath
		self.preferences = get_prefs()
		self.auto_hide_text_when_finished = self.preferences.auto_hide_text_when_finished
		self.wait_before_hiding = self.preferences.wait_before_hiding
		self.current_files_to_import = []
		self.current_filenames = []
		self.operation_processed = 0
		self.processing = False
		self.show_scroll_text = False
		self.start_time = 0
		self.total_imported_size = 0
		self.umi_settings = context.scene.umi_settings
		self.umi_settings.umi_import_settings.umi_import_cancelled = False
		LOG.revert_parameters()
		LOG.show_log = self.preferences.show_log_on_3d_view
		LOG.esc_message = '[Esc] to Cancel'
		LOG.message_offset = 15

		for f in COMPATIBLE_FORMATS.formats:
			exec('self.{}_format = TILA_umi_format_handler(import_format="{}", context=cont)'.format(f[0], f[0]), {'self':self, 'TILA_umi_format_handler':TILA_umi_format_handler, 'cont':context})

		if not path.exists(self.current_blend_file):
			LOG.warning('Blender file not saved')
			self.save_file_after_import = False
			autosave = path.join(bpy.utils.user_resource(resource_type='AUTOSAVE', create=True), 'umi_autosave_' + time.strftime('%Y-%m-%d-%H-%M-%S') + '.blend')
		else:
			autosave = path.splitext(self.current_blend_file)[0] + "_bak" + path.splitext(self.current_blend_file)[1]

		self.blend_backup_file = autosave
		
		compatible_extensions = self.get_compatible_extensions()
		self.folder = (os.path.dirname(self.filepath))
		self.filepaths = [path.join(self.folder, f.name) for f in self.files if path.splitext(f.name)[1].lower() in compatible_extensions]

		if not len(self.filepaths):
			message = "No compatible file selected"
			LOG.error(message)
			self.report({'ERROR'}, message)
			return {'CANCELLED'}

		self.filepaths.reverse()
		self.number_of_files = len(self.filepaths)
		self.number_of_commands = len(self.umi_settings.umi_operators)
		self.number_of_operations = self.number_of_files

		LOG.info("{} compatible file(s) found".format(len(self.filepaths)))
		LOG.separator()

		self.view_layer = bpy.context.view_layer
		self.root_collection = bpy.context.collection
		self.current_file_number = 0


		self.umi_settings.umi_ready_to_import = False

		self.store_formats_to_import()
		self.objects_to_process = []
		self.current_object_to_process = None
		self.operator_list = [{'name':'operator', 'operator': o.operator} for o in self.umi_settings.umi_operators]

		args = (context,)
		self._handle = bpy.types.SpaceView3D.draw_handler_add(LOG.draw_callback_px, args, 'WINDOW', 'POST_PIXEL')

		wm = context.window_manager
		self._timer = wm.event_timer_add(0.1, window=context.window)
		wm.modal_handler_add(self)
		return {'RUNNING_MODAL'}
	
	def next_file(self):
		self.current_files_to_import = []
		self.current_filenames = []
		self.current_batch_size = 0
		for f in range(self.import_simultaneously_count):
			if not len(self.filepaths):
				return
			index = len(self.filepaths) - 1
			filepath = self.filepaths[index]
			current_file_size = self.get_filesize(filepath)
			self.current_batch_size += current_file_size
			self.total_imported_size += current_file_size

			if self.max_batch_size:
				if self.current_batch_size > self.max_batch_size:
					if len(self.current_files_to_import):
						self.current_batch_size -= current_file_size
						self.total_imported_size -= current_file_size
						return
					
					self.current_files_to_import.append(self.filepaths.pop())
					self.current_file_number += 1
					return
				
			self.current_files_to_import.append(self.filepaths.pop())
			self.current_file_number += 1
			
		
	def cancel(self, context):
		self.canceled = True
		if self._timer is not None:
			wm = context.window_manager
			wm.event_timer_remove(self._timer)
