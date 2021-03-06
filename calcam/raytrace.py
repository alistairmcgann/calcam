'''
* Copyright 2015-2017 European Atomic Energy Community (EURATOM)
*
* Licensed under the EUPL, Version 1.1 or - as soon they
  will be approved by the European Commission - subsequent
  versions of the EUPL (the "Licence");
* You may not use this work except in compliance with the
  Licence.
* You may obtain a copy of the Licence at:
*
* https://joinup.ec.europa.eu/software/page/eupl
*
* Unless required by applicable law or agreed to in
  writing, software distributed under the Licence is
  distributed on an "AS IS" basis,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
  express or implied.
* See the Licence for the specific language governing
  permissions and limitations under the Licence.
'''

""" 
Ray tracing tools for CalCam_py

Written by Scott Silburn
2015-05-17
"""


import vtk
import numpy as np
import datetime
import time
import sys
import os
import paths
from scipy.io.netcdf import netcdf_file
import coordtransformer
import random


""" 
Ray Caster class.
Given a CAD model and camera fit, does ray casting.
"""
class RayCaster:
	
    def __init__(self,FitResults = None,CADModel = None,verbose=True):
		
        self.verbose = verbose
        self.vtkCellLocator = None
        self.machine_name = None
        self.fitresults = None

        if FitResults is not None:
            self.fitresults = FitResults   
		
        if CADModel is not None:
            self.set_cadmodel(CADModel)



    def set_cadmodel(self,CADModel):
        # CAD model name
        self.machine_name = CADModel.machine_name
        # Ray length to use
        self.max_ray_length = CADModel.max_ray_length

        if self.verbose:
            print('[Calcam RayCaster] Getting CAD model octree...')

        # Get the CAD model's octree
        self.vtkCellLocator = CADModel.get_vtkCellLocator()

        if self.verbose:
            print('[Calcam RayCaster] Done.'.format(self.machine_name))

    def set_calibration(self,FitResults):	
        self.fitresults = FitResults
		

    def raycast_pixels(self,x=None,y=None,binning=1,Coords='Display'):

        if self.fitresults is None:
            raise Exception('Camera fit results not set in RayCaster!')
        if self.vtkCellLocator is None:
            raise Exception('CAD model not set in RayCaster!')

        # If no pixels are specified, do the whole chip at the specified binning level.
        fullchip = False
        if x is None and y is None:
            fullchip = True
            if Coords.lower() == 'display':
                xl = np.linspace( (binning-1.)/2,float(self.fitresults.image_display_shape[0]-1)-(binning-1.)/2,(1+float(self.fitresults.image_display_shape[0]-1))/binning)
                yl = np.linspace( (binning-1.)/2,float(self.fitresults.image_display_shape[1]-1)-(binning-1.)/2,(1+float(self.fitresults.image_display_shape[1]-1))/binning)
                x,y = np.meshgrid(xl,yl)
            else:
                xl = np.linspace( (binning-1.)/2,float(self.fitresults.transform.x_pixels-1)-(binning-1.)/2,(1+float(self.fitresults.transform.x_pixels-1))/binning)
                yl = np.linspace( (binning-1.)/2,float(self.fitresults.transform.y_pixels-1)-(binning-1.)/2,(1+float(self.fitresults.transform.y_pixels-1))/binning)
                x,y = np.meshgrid(xl,yl)
                x,y = self.fitresults.transform.original_to_display_coords(x,y)
            valid_mask = np.ones(x.shape,dtype=bool)
        else:
            if np.shape(x) != np.shape(y):
                raise ValueError('x and y arrays must be the same shape!')
            valid_mask = np.logical_and(np.isnan(x) == 0 , np.isnan(y) == 0 )
            if Coords.lower() == 'original':
                x,y = self.fitresults.transform.original_to_display_coords(x,y)

        Results = RayData()
        Results.ResultType = 'PixelRayCast'
        Results.fullchip = fullchip
        Results.x = np.copy(x).astype('float')
        Results.x[valid_mask == 0] = 0
        Results.y = np.copy(y).astype('float')
        Results.y[valid_mask == 0] = 0
        Results.transform = self.fitresults.transform

        orig_shape = np.shape(Results.x)
        Results.x = np.reshape(Results.x,np.size(Results.x),order='F')
        Results.y = np.reshape(Results.y,np.size(Results.y),order='F')
        valid_mask = np.reshape(valid_mask,np.size(valid_mask),order='F')
        totpx = np.size(Results.x)


        # New results object to store results
        if fullchip:
            Results.binning = binning
        else:
            Results.binning = None

        Results.ray_end_coords = np.ndarray([np.size(x),3])

        # Line of sight directions
        LOSDir = self.fitresults.get_los_direction(Results.x,Results.y,Coords='Display')
        Results.ray_start_coords = self.fitresults.get_pupilpos(Results.x,Results.y,Coords='Display')

		
        if self.verbose:
            now = datetime.datetime.now()
            print(datetime.datetime.now().strftime('[Calcam RayCaster] Started casting {:d} rays at %Y-%m-%d %H:%M'.format(np.size(x))))


        # Some variables to give to VTK becasue of its annoying C-like interface
        t = vtk.mutable(0)
        pos = np.zeros(3)
        coords = np.zeros(3)
        subid = vtk.mutable(0)

        starttime = time.time()
        etime_printed = False
        n_done = 0
        
        # We will do the ray casting in a random order,
        # purely to get better time remaining estimation.
        inds = list(range(np.size(x)))
        random.shuffle(inds)
        
        for ind in inds:

            if not valid_mask[ind]:
                Results.ray_end_coords[ind,:] = np.nan
                Results.ray_start_coords[ind,:] = np.nan
                continue

            # Do the raycast and put the result in the output array
            rayend = Results.ray_start_coords[ind] + self.max_ray_length * LOSDir[ind]
            retval = self.vtkCellLocator.IntersectWithLine(Results.ray_start_coords[ind],rayend,1.e-6,t,pos,coords,subid)

            if abs(retval) > 0:
                Results.ray_end_coords[ind,:] = pos[:]
            else:
                Results.ray_end_coords[ind,:] = rayend

            n_done = n_done + 1
            # Progress printing stuff
            if self.verbose and not etime_printed:
                if time.time() - starttime > 10:
                    est_time = (time.time() - starttime) / n_done * np.size(x)
                    if est_time > 15:
                        est_time_string = ''
                        if est_time > 3600:
                            est_time_string = est_time_string + '{:.0f} hr '.format(np.floor(est_time/3600))
                        if est_time > 600:
                            est_time_string = est_time_string + '{:.0f} min.'.format((est_time - 3600*np.floor(est_time/3600))/60)
                        elif est_time > 60:
                            est_time_string = est_time_string + '{:.0f} min {:.0f} sec.'.format(np.floor(est_time/60),est_time % 60)
                        else:
                            est_time_string ='{:.0f} sec.'.format(est_time)

                        print('[Calcam RayCaster] Estimated calculation time: ' + est_time_string)
                    etime_printed = True

        if self.verbose:
            tot_time = time.time() - starttime
            time_string = ''
            if tot_time > 3600:
                time_string = time_string + '{0:.0f} hr '.format(np.floor(tot_time / 3600))
            if tot_time > 60:
                time_string = time_string + '{0:.0f} min '.format(np.floor( (tot_time - 3600*np.floor(tot_time / 3600))  / 60))
            time_string = time_string + '{0:.0f} sec. '.format( tot_time - 60*np.floor(tot_time / 60) )

            print('[Calcam RayCaster] Finished casting {:d} rays in '.format(np.size(x)) + time_string)

        Results.x[valid_mask == 0] = np.nan
        Results.y[valid_mask == 0] = np.nan

        Results.ray_end_coords = np.reshape(Results.ray_end_coords,orig_shape + (3,),order='F')
        Results.ray_start_coords = np.reshape(Results.ray_start_coords,orig_shape + (3,),order='F')
        Results.x = np.reshape(Results.x,orig_shape,order='F')
        Results.y = np.reshape(Results.y,orig_shape,order='F')

        return Results



# Class for storing ray data
class RayData:
    def __init__(self,filename=None):
        self.ray_end_coords = None
        self.ray_start_coords = None
        self.binning = None
        self.transform = None
        self.fullchip = None
        self.x = None
        self.y = None
        self.ResultType = None
        if filename is not None:
            self.load(filename)


    # Save to a netCDF file
    def save(self,SaveName):

        f = netcdf_file(os.path.join(paths.raydata,SaveName + '.nc'),'w')
        setattr(f,'history','CalCam_py output file')
        setattr(f,'image_transform_actions',"['" + "','".join(self.transform.transform_actions) + "']")
        setattr(f,'ResultType',self.ResultType)

        pointdim = f.createDimension('pointdim',3)

        if len(self.x.shape) == 2:
            udim = f.createDimension('udim',self.x.shape[1])
            vdim = f.createDimension('vdim',self.x.shape[0])
            rayhit = f.createVariable('RayEndCoords','f4',('vdim','udim','pointdim'))
            raystart = f.createVariable('RayStartCoords','f4',('vdim','udim','pointdim'))
            x = f.createVariable('PixelXLocation','i4',('vdim','udim'))
            y = f.createVariable('PixelYLocation','i4',('vdim','udim'))
			
            rayhit[:,:,:] = self.ray_end_coords
            raystart[:,:,:] = self.ray_start_coords
            x[:,:] = self.x
            y[:,:] = self.y
        elif len(self.x.shape) == 1:
            udim = f.createDimension('udim',self.x.size)
            rayhit = f.createVariable('RayEndCoords','f4',('udim','pointdim'))
            raystart = f.createVariable('RayStartCoords','f4',('udim','pointdim'))
            x = f.createVariable('PixelXLocation','i4',('udim',))
            y = f.createVariable('PixelYLocation','i4',('udim',))

            rayhit[:,:] = self.ray_end_coords
            raystart[:,:] = self.ray_start_coords

            x[:] = self.x
            y[:] = self.y
        else:
            raise Exception('Cannot save RayData with >2D x and y arrays!')

        binning = f.createVariable('Binning','i4',())

        if self.binning is not None:
            binning.assignValue(self.binning)
        else:
            binning.assignValue(0)

        pixelsdim = f.createDimension('pixelsdim',2)

        xpx = f.createVariable('image_original_shape','i4',('pixelsdim',))
        xpx[:] = [self.transform.x_pixels,self.transform.y_pixels]

        pixelaspect = f.createVariable('image_original_pixel_aspect','f4',())
        pixelaspect.assignValue(self.transform.pixel_aspectratio)

        binning.units = 'pixels'
        raystart.units = 'm'
        rayhit.units = 'm'
        x.units = 'pixels'
        y.units = 'pixels'
        f.close()


    # Load from a netCDF file
    def load(self,SaveName):
        f = netcdf_file(os.path.join(paths.raydata,SaveName + '.nc'), 'r',mmap=False)
        self.ResultType = f.ResultType
        self.ray_end_coords = f.variables['RayEndCoords'].data
        self.ray_start_coords = f.variables['RayStartCoords'].data
        self.binning = f.variables['Binning'].data
        if self.binning == 0:
            self.binning = None
            self.fullchip = False
        else:
            self.fullchip = True

        self.x = f.variables['PixelXLocation'].data
        self.y = f.variables['PixelYLocation'].data

        self.transform = coordtransformer.CoordTransformer()
        self.transform.set_transform_actions(eval(f.image_transform_actions))
        self.transform.x_pixels = f.variables['image_original_shape'][0]
        self.transform.y_pixels = f.variables['image_original_shape'][1]
        self.transform.pixel_aspectratio = f.variables['image_original_pixel_aspect'].data

        f.close()

    # Return array of the sight-line length for each pixel.
    def get_ray_lengths(self,x=None,y=None,PositionTol = 3,Coords='Display'):

        # Work out ray lengths for all raytraced pixels
        RayLength = np.sqrt(np.sum( (self.ray_end_coords - self.ray_start_coords) **2,axis=-1))
        # If no x and y given, return them all
        if x is None and y is None:
            if self.fullchip:
                if Coords.lower() == 'display':
                    return RayLength
                else:
                    return self.transform.display_to_original_image(RayLength,binning=self.binning)
            else:
                return RayLength
        else:
            if self.x is None or self.y is None:
                raise Exception('This ray data does not have x and y pixel indices!')

            # Otherwise, return the ones at given x and y pixel coords.
            if np.shape(x) != np.shape(y):
                raise ValueError('x and y arrays must be the same shape!')
            else:

                if Coords.lower() == 'original':
                    x,y = self.transform.original_to_display_coords(x,y)

                orig_shape = np.shape(x)
                x = np.reshape(x,np.size(x),order='F')
                y = np.reshape(y,np.size(y),order='F')
                RL = np.zeros(np.shape(x))
                RayLength = RayLength.flatten()
                xflat = self.x.flatten()
                yflat = self.y.flatten()
                for pointno in range(x.size):
                    if np.isnan(x[pointno]) or np.isnan(y[pointno]):
                        RL[pointno] = np.nan
                        continue

                    deltaX = xflat - x[pointno]
                    deltaY = yflat - y[pointno]
                    deltaR = np.sqrt(deltaX**2 + deltaY**2)
                    if np.nanmin(deltaR) <= PositionTol:
                        RL[pointno] = RayLength[np.nanargmin(deltaR)]
                    else:
                        raise Exception('No ray-traced pixel within PositionTol of requested pixel!')
                return np.reshape(RL,orig_shape,order='F')


    # Return unit vectors of sight-line direction for each pixel.
    def get_ray_directions(self,x=None,y=None,PositionTol=3,Coords='Display'):
	 	
        lengths = self.get_ray_lengths()
        dirs = (self.ray_end_coords - self.ray_start_coords) / np.repeat(lengths.reshape(np.shape(lengths)+(1,)),3,axis=-1)

        if x is None and y is None:
            if self.fullchip:
                if Coords.lower() == 'display':
                    return dirs
                else:
                    return self.transform.display_to_original_image(dirs,binning=self.binning)
            else:
                return dirs
        else:
            if self.x is None or self.y is None:
                raise Exception('This ray data does not have x and y pixel indices!')
            if np.shape(x) != np.shape(y):
                raise ValueError('x and y arrays must be the same shape!')
            else:

                if Coords.lower() == 'original':
                    x,y = self.transform.original_to_display_coords(x,y)

                oldshape = np.shape(x)
                x = np.reshape(x,np.size(x),order='F')
                y = np.reshape(y,np.size(y),order='F')
                [dirs_X,dirs_Y,dirs_Z] = np.split(dirs,3,-1)
                dirs_X = dirs_X.flatten()
                dirs_Y = dirs_Y.flatten()
                dirs_Z = dirs_Z.flatten()
                xflat = self.x.flatten()
                yflat = self.y.flatten()
                Xout = np.zeros(np.shape(x))
                Yout = np.zeros(np.shape(x))
                Zout = np.zeros(np.shape(x))
                for pointno in range(x.size):
                    deltaX = xflat - x[pointno]
                    deltaY = yflat - y[pointno]
                    deltaR = np.sqrt(deltaX**2 + deltaY**2)
                    if np.min(deltaR) <= PositionTol:
                        Xout[pointno] = dirs_X[np.argmin(deltaR)]
                        Yout[pointno] = dirs_Y[np.argmin(deltaR)]
                        Zout[pointno] = dirs_Z[np.argmin(deltaR)]
                    else:
                        raise Exception('No ray-traced pixel within PositionTol of requested pixel!')
                out = np.hstack([Xout,Yout,Zout])

                return np.reshape(out,oldshape + (3,),order='F')
