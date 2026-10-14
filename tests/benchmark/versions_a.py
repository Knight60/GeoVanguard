import arcpy, platform
i = arcpy.GetInstallInfo(); print('ArcGIS Pro', i['Version'], 'build', i.get('BuildNumber')); print('ArcGIS Python', platform.python_version()); print('license', arcpy.ProductInfo())
