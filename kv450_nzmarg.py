##############################################################
# Likelihood for the KiDS+VIKING-450 correlation functions   #
##############################################################
#
# Originally set up by Antonio J. Cuesta and J. Lesgourgues
# for CFHTLenS data, by adapting Benjamin Audren's Monte Python
# likelihood euclid_lensing and Adam J Moss's CosmoMC likelihood
# for weak lensing (adapted itself from JL's CosmoMC likelihood
# for the COSMOS).
#
# Adjusted for KV450 correlation function data from Hildebrandt
# et al. 2018 (arXiv:1812.06076) by Fabian Koehlinger and Hendrik
# Hildebrandt.
#
# Data available from:
#
# http://kids.strw.leidenuniv.nl/sciencedata.php
#
# ATTENTION:
# This likelihood only produces valid results for \Omega_k = 0,
# i.e. flat cosmologies!
##############################################################

from montepython.likelihood_class import Likelihood
import io_mp
import parser_mp

#import scipy.integrate
from scipy import interpolate as itp
from scipy import special
from scipy.linalg import cholesky, solve_triangular
import os
import numpy as np
import math
import scipy.linalg
import time
class kv450_nzmarg(Likelihood):

    def __init__(self, path, data, command_line):

        Likelihood.__init__(self, path, data, command_line)

        # Check if the data can be found, although we don't actually use that
        # particular file but take it as a placeholder for the folder
        try:
            fname = os.path.join(self.data_directory, 'DATA_VECTOR/KV450_xi_pm_tomographic_data_vector.dat')
            parser_mp.existing_file(fname)
        except:
            raise io_mp.ConfigurationError('KiDS+VIKING-450 CF data not found. Download the data at '
                                           'http://kids.strw.leidenuniv.nl/sciencedata.php '
                                           'and specify path to data through the variable '
                                           'kv450_cf_likelihood_public.data_directory in '
                                           'the .data file. See README in likelihood folder '
                                           'for further instructions.')

        # create folder for Monte Python related output:
        folder_name = os.path.join(self.data_directory, 'FOR_MONTE_PYTHON')
        if not os.path.isdir(folder_name):
            os.makedirs(folder_name)
            print('Created folder for Monte Python related data files: \n', folder_name, '\n')

        # for loading of Nz-files:
        self.z_bins_min = [0.1, 0.3, 0.5, 0.7, 0.9]
        self.z_bins_max = [0.3, 0.5, 0.7, 0.9, 1.2]

        # number of angular bins in which xipm is measured
        # we always load the full data vector with 9 data points for xi_p and
        # xi_m each; they are cut to the fiducial scales (or any arbitrarily
        # defined scales with the 'cut_values.dat' files!
        self.ntheta = 9

        # this was not used in any of the KV450 analyses, but we keep it in the
        # likelihood and just turn it off!
        self.bootstrap_photoz_errors = False

        # Force the cosmological module to store Pk for redshifts up to
        # max(self.z) and for k up to k_max
        self.need_cosmo_arguments(data, {'output': 'mPk'})
        self.need_cosmo_arguments(data, {'P_k_max_h/Mpc': self.k_max_h_by_Mpc})
        self.need_cosmo_arguments(data, {'l_switch_limber_for_nc_local_over_z': 10000})
        self.need_cosmo_arguments(data, {'l_switch_limber_for_nc_los_over_z': 2000})
        # l_switch_limber_for_nc_local_over_z, l_switch_limber_for_nc_los_over_z; for instance, add them to the input file with values 10000 and 2000
        ## Compute non-linear power spectrum if requested
        #if (self.use_halofit):
        # it seems like HMcode needs the full argument to work...
        if self.method_non_linear_Pk in ['halofit', 'HALOFIT', 'Halofit', 'hmcode', 'Hmcode', 'HMcode', 'HMCODE']:
            self.need_cosmo_arguments(data, {'non linear': self.method_non_linear_Pk})
            print('Using {:} to obtain the non-linear corrections for the matter power spectrum, P(k, z)! \n'.format(self.method_non_linear_Pk))
        else:
            print('Only using the linear P(k, z) for ALL calculations \n (check keywords for "method_non_linear_Pk"). \n')

        # TODO: move min_kmax_hmc to data-file?!
        # might not be really necessary; I didn't see a difference in the P(k, z) ratios between
        # HMcode complaining about k_max being too low and not complaining at all...
        if self.method_non_linear_Pk in ['hmcode', 'Hmcode', 'HMcode', 'HMCODE']:
            #self.need_cosmo_arguments(data, {'hmcode_min_k_max': 1000.})
            min_kmax_hmc = 170.
            if self.k_max_h_by_Mpc < min_kmax_hmc:
                self.need_cosmo_arguments(data, {'P_k_max_h/Mpc': min_kmax_hmc})
                #print "Your choice of k_max_h_by_Mpc is too small for HMcode. \n Requested P_k_max_h/Mpc now up to k = {:.2f} h/Mpc \n This does NOT influence the scale above".format(min_kmax_hmc)

        # This is for Cl-integration only!
        # Define array of l values, and initialize them
        # It is a logspace
        # find nlmax in order to reach lmax with logarithmic steps dlnl
        self.nlmax = np.int(np.log(self.lmax) / self.dlnl) + 1
        # redefine slightly dlnl so that the last point is always exactly lmax
        self.dlnl = np.log(self.lmax) / (self.nlmax - 1)
        self.l = np.exp(self.dlnl * np.arange(self.nlmax))
        self.nzbins = len(self.z_bins_min)
        self.nzcorrs = int(self.nzbins * (self.nzbins + 1) / 2)

        # Each redshift distribution is modelled modelled as a comb, i.e. a sum of modified gaussians
        # Calculate the number of correlations between comb components
        self.nzcorrs_gaussians = int(self.ngaussians * (self.ngaussians+1) / 2)
        # Calculate the number of fit parameters
        self.nfitparameters = self.nzbins * self.ngaussians
        # Use number of comb components and redshift range to calculate the means and sigma of the n_components
        self.sigma_comb = (self.zmax - self.zmin) / self.ngaussians
        self.mean_gaussians = self.zmin+np.arange(self.ngaussians)*self.sigma_comb
        # read in public data vector:
        temp = self.__load_public_data_vector()
        self.theta_bins = temp[:, 0]
        if (np.sum(
                (self.theta_bins[:self.ntheta] -
                    self.theta_bins[self.ntheta:])**2) > 1e-6):
                raise io_mp.LikelihoodError(
                    'The angular values at which xi_p and xi_m '
                    'are observed do not match.')

        # create the data-vector:
        # xi_obs = {xi1(theta1, z_11)...xi1(theta_k, z_11), xi2(theta_1, z_11)...
        #           xi2(theta_k, z_11);...; xi1(theta1, z_nn)...xi1(theta_k, z_nn),
        #           xi2(theta_1, z_nn)... xi2(theta_k, z_nn)}
        self.xi_obs = self.__get_xi_obs(temp[:, 1:])

        # load the full covariance matrix:
        covmat = self.__load_public_cov_mat()

        # Read angular cut values
        if self.use_cut_theta:
            cut_values = np.zeros((self.nzbins, 2))
            cutvalues_file_path = os.path.join(self.data_directory, 'SUPPLEMENTARY_FILES/CUT_VALUES/' + self.cutvalues_file)
            if os.path.exists(cutvalues_file_path):
                cut_values = np.loadtxt(cutvalues_file_path)
            else:
                raise io_mp.LikelihoodError('File not found:\n {:} \n Check that requested file exists in the following folder: \n {:}'.format(cutvalues_file_path, self.data_directory + 'SUPPLEMENTARY_FILES/CUT_VALUES/'))

        # Compute theta mask
        if self.use_cut_theta:
            mask = self.__get_mask(cut_values)
        else:
            mask = np.ones(2 * self.nzcorrs * self.ntheta)

        self.mask_indices = np.where(mask == 1)[0]
        self.mask_suffix = self.cutvalues_file[:-4]

        # write out masked data vector:
        self.__write_out_vector_in_list_format(self.xi_obs, fname_prefix='KV450_xi_pm')

        # apply mask also to covariance matrix
        covmat = covmat[np.ix_(self.mask_indices, self.mask_indices)]
        # save masked covariance in list format:
        fname = os.path.join(self.data_directory, 'FOR_MONTE_PYTHON/Cov_mat_inc_m_cut_to_{:}.dat'.format(self.mask_suffix))
        idx2, idx1 = np.meshgrid(range(covmat.shape[0]), range(covmat.shape[0]))
        header = ' i       j    Cov(i,j) including m uncertainty'
        np.savetxt(fname, np.column_stack((idx1.flatten() + 1, idx2.flatten() + 1, covmat.flatten())), header=header, delimiter='\t', fmt=['%4i', '%4i', '%.15e'])
        print('Saved covariance matrix (incl. shear calibration uncertainty) cut down to scales as specified in {:} to: \n'.format(self.cutvalues_file), fname, '\n')

        # precompute Cholesky transform for chi^2 calculation:
        self.cholesky_transform = cholesky(covmat, lower=True)
        self.covmat = covmat
        # load theta-dependent c-term function if requested
        # file is assumed to contain values for the same theta values as used
        # for xi_pm!
        if self.use_cterm_function:
            fname = os.path.join(self.data_directory, 'SUPPLEMENTARY_FILES/KV450_xi_pm_c_term.dat')
            # function is measured over same theta scales as xip, xim
            self.xip_c_per_zbin, self.xim_c_per_zbin = np.loadtxt(fname, usecols=(3, 4), unpack=True)
            print('Loaded (angular) scale-dependent c-term function from: \n', fname, '\n')
            #print self.xip_c_per_zbin.shape

        if self.zmin == 0:
            self.z_p = np.linspace(0.0001, self.zmax, self.nzmax)
        else:
            self.z_p = np.linspace(self.zmin, self.zmax, self.nzmax)
        dz = np.diff(self.z_p)[5]
        # load amplitudes of redshift calibration from file
        self.A = np.loadtxt(self.amp_file,unpack=True).T
        self.A = np.exp(self.A)

        if self.simple_approximation or self.full_marginalisation:
            self.calibration_matrix = np.loadtxt(self.calibration_matrix_file, unpack = True).T
        # p_i: redshift distributions of comb components evaluated on z_p grid
        self.p_i=np.array([self.K(self.z_p,self.mean_gaussians[i],self.sigma_comb) for i in range(self.ngaussians)]).T

        self.zmax = self.z_p.max()
        self.need_cosmo_arguments(data, {'z_max_pk': self.zmax})

        # Fill array of discrete z values
        # self.z = np.linspace(0, self.zmax, num=self.nzmax)

        '''
        ################
        # Noise spectrum
        ################

        # Number of galaxies per steradian
        self.noise = 3600. * self.gal_per_sqarcmn * (180. / math.pi)**2

        # Number of galaxies per steradian per bin
        self.noise = self.noise / self.nzbins

        # Noise spectrum (diagonal in bin*bin space, independent of l and Bin)
        self.noise = self.rms_shear**2/self.noise
        '''

        ################################################
        # discrete theta values (to convert C_l to xi's)
        ################################################

        if self.use_theory_binning:
            thetamin = np.min(self.theta_bin_min_val) * 0.8
            thetamax = np.max(self.theta_bin_max_val) * 1.2
        else:
            thetamin = np.min(self.theta_bins) * 0.8
            thetamax = np.max(self.theta_bins) * 1.2

        if self.integrate_Bessel_with == 'fftlog':
            try:
                import pycl2xi.fftlog as fftlog

            except:
                print('FFTLog was requested as integration method for the Bessel functions but is not installed. \n Download it from "https://github.com/tilmantroester/pycl2xi" and follow the installation instructions there (also requires the fftw3 library). \n Aborting run now... \n')
                exit()

            # this has to be declared a self, otherwise fftlog won't be available
            self.Cl2xi = fftlog.Cl2xi

        if self.integrate_Bessel_with == 'brute_force':
            # we redefine these settings so that lll for Bessel integration corresponds
            # to range that was used when comparing to CCL
            self.xmax = 100.
            self.dx_below_threshold = 0.02
            self.dx_above_threshold = 0.07
            self.dx_threshold = 0.2
            self.dlntheta = 0.12
            # self.xmax = 200.
            # self.dx_below_threshold = 0.01
            # self.dx_above_threshold = 0.035
            # self.dx_threshold = 0.1
            # self.dlntheta = 0.06

        #### non splines
        self.theta = self.theta_bins[:self.ntheta]
        self.nthetatot = len(self.theta)
        #### splines
        # self.nthetatot = np.ceil(math.log(thetamax / thetamin) / self.dlntheta) + 1
        # self.nthetatot = np.int32(self.nthetatot)
        # self.theta = np.zeros(self.nthetatot, 'float64')
        # define an array of thetas
        # for it in range(self.nthetatot):
            # self.theta[it] = thetamin * math.exp(self.dlntheta * it)
        ###
        self.a2r = math.pi / (180. * 60.)

        if self.integrate_Bessel_with in ['brute_force', 'cut_off']:

            ################################################################
            # discrete l values used in the integral to convert C_l to xi's)
            ################################################################

            # l = x / theta / self.a2r
            # x = l * theta * self.a2r

            # We start by considering the largest theta, theta[-1], and for that value we infer
            # a list of l's from the requirement that corresponding x values are spaced linearly with a given stepsize, until xmax.
            # Then we loop over smaller theta values, in decreasing order, and for each of them we complete the previous list of l's,
            # always requiuring the same dx stepsize (so that dl does vary) up to xmax.
            #
            # We first apply this to a running value ll, in order to count the total numbner of ll's, called nl.
            # Then we create the array lll[nl] and we fill it with the same values.
            #
            # we also compute on the fly the critical index il_max[it] such that ll[il_max[it]]*self.theta[it]*self.a2r
            # is the first value of x above xmax

            ll=1.
            il=0
            while (ll*self.theta[-1]*self.a2r < self.dx_threshold):
                ll += self.dx_below_threshold/self.theta[-1]/self.a2r
                il += 1
            for it  in range(self.nthetatot):
                while (ll*self.theta[self.nthetatot-1-it]*self.a2r < self.xmax) and (ll+self.dx_above_threshold/self.theta[self.nthetatot-1-it]/self.a2r < self.lmax):
                    ll += self.dx_above_threshold/self.theta[self.nthetatot-1-it]/self.a2r
                    il += 1
            self.nl = il+1

            self.lll = np.zeros(self.nl, 'float64')
            self.il_max = np.zeros(self.nthetatot, 'int')
            il=0
            self.lll[il]=1.
            while (self.lll[il]*self.theta[-1]*self.a2r < self.dx_threshold):
                il += 1
                self.lll[il] = self.lll[il-1] + self.dx_below_threshold/self.theta[-1]/self.a2r
            for it  in range(self.nthetatot):
                while (self.lll[il]*self.theta[self.nthetatot-1-it]*self.a2r < self.xmax) and (self.lll[il] + self.dx_above_threshold/self.theta[self.nthetatot-1-it]/self.a2r < self.lmax):
                    il += 1
                    self.lll[il] = self.lll[il-1] + self.dx_above_threshold/self.theta[self.nthetatot-1-it]/self.a2r
                self.il_max[self.nthetatot-1-it] = il

            # finally we compute the array l*dl that will be used in the trapezoidal integration
            # (l is a factor in the integrand [l * C_l * Bessel], and dl is like a weight)
            self.ldl = np.zeros(self.nl, 'float64')
            self.ldl[0]=self.lll[0]*0.5*(self.lll[1]-self.lll[0])
            for il in range(1,self.nl-1):
                self.ldl[il]=self.lll[il]*0.5*(self.lll[il+1]-self.lll[il-1])
            self.ldl[-1]=self.lll[-1]*0.5*(self.lll[-1]-self.lll[-2])
        else:
            # this is sufficient (FFTLog only uses 5k points internally anyways...)
            ell_lin = np.arange(1., 501., 1)
            ell_log = np.logspace(np.log10(501.), np.log10(self.lmax), 5000 - len(ell_lin))
            self.lll = np.concatenate((ell_lin, ell_log))
            # linspace --> overkill and too slow!
            #self.lll = np.arange(1., self.lmax + 1., 1)
            self.nl = self.lll.size

        #print self.lll.min(), self.lll.max(), self.lll.shape
        #exit()

        # here we set up arrays and some integrations necessary for the theory binning:
        if self.use_theory_binning:

            if self.read_weight_func_for_binning:
                fname = os.path.join(self.data_directory, self.theory_weight_func_file)
                thetas, weights = np.loadtxt(fname, unpack=True)
                self.theory_weight_func = itp.splrep(thetas, weights)
            else:
                thetas = np.linspace(self.theta_bin_min_val, self.theta_bin_max_val, self.ntheta * int(self.theta_nodes_theory))
                weights = self.a2r * thetas * self.theory_binning_const
                self.theory_weight_func = itp.splrep(thetas, weights)

            # first get the theta-bin borders based on ntheta and absolute min and absolute max values
            a = np.linspace(np.log10(self.theta_bin_min_val), np.log10(self.theta_bin_max_val), self.ntheta + 1)
            theta_bins = 10.**a
            self.theta_bin_min = theta_bins[:-1]
            self.theta_bin_max = theta_bins[1:]

            self.int_weight_func = np.zeros(self.ntheta)
            self.thetas_for_theory_binning = np.zeros((self.ntheta, int(self.theta_nodes_theory)))
            for idx_theta in range(self.ntheta):
                theta = np.linspace(self.theta_bin_min[idx_theta], self.theta_bin_max[idx_theta], int(self.theta_nodes_theory))
                dtheta = (theta[1:] - theta[:-1]) * self.a2r

                weight_func_integrand = itp.splev(theta, self.theory_weight_func)

                self.int_weight_func[idx_theta] = np.sum(0.5 * (weight_func_integrand[1:] + weight_func_integrand[:-1]) * dtheta)
                # for convenience:
                self.thetas_for_theory_binning[idx_theta, :] = theta

        #####################################################################
        # Allocation of various arrays filled and used in the function loglkl
        #####################################################################
        # most self.nzcorr have been exchanged for self.nzcorrs_gaussians to calculate the Cl for all comb components
        self.r = np.zeros(self.nzmax, 'float64')
        self.dzdr = np.zeros(self.nzmax, 'float64')
        self.g = np.zeros((self.nzmax, self.ngaussians), 'float64')
        self.pk = np.zeros((self.nlmax, self.nzmax), 'float64')
        self.pk_lin = np.zeros((self.nlmax, self.nzmax), 'float64')
        self.k_sigma = np.zeros(self.nzmax, 'float64')
        self.alpha = np.zeros((self.nlmax, self.nzmax), 'float64')
        if 'epsilon' in self.use_nuisance:
            self.E_th_nu = np.zeros((self.nlmax, self.nzmax), 'float64')
        self.Cl_integrand = np.zeros((self.nzmax, self.nzcorrs_gaussians), 'float64')
        self.Cl = np.zeros((self.nlmax, self.nzcorrs_gaussians), 'float64')
        '''
        if self.theoretical_error != 0:
            self.El_integrand = np.zeros((self.nzmax, self.nzcorrs),'float64')
            self.El = np.zeros((self.nlmax, self.nzcorrs), 'float64')
        '''
        self.spline_Cl = np.empty(self.nzcorrs_gaussians, dtype=(list, 3))
        self.xi1 = np.zeros((self.nthetatot, self.nzcorrs_gaussians), 'float64')
        self.xi2 = np.zeros((self.nthetatot, self.nzcorrs_gaussians), 'float64')
        self.Cll = np.zeros((self.nzcorrs_gaussians, self.nl), 'float64')
        self.BBessel0 = np.zeros(self.nl, 'float64')
        self.BBessel4 = np.zeros(self.nl, 'float64')
        self.xi1_theta = np.empty(self.nzcorrs, dtype=(list, 3))
        self.xi2_theta = np.empty(self.nzcorrs, dtype=(list, 3))
        self.xi = np.zeros(np.size(self.xi_obs), 'float64')

        return


    def __load_public_data_vector(self):
        """
        Helper function to read in the full data vector from public KiDS+VIKING-450
        release and to bring it into the input format used in the original
        CFHTLenS likelihood.
        """

        # plus one for theta-column
        data_xip = np.zeros((self.ntheta, self.nzcorrs + 1))
        data_xim = np.zeros((self.ntheta, self.nzcorrs + 1))
        idx_corr = 0
        for zbin1 in range(self.nzbins):
            for zbin2 in range(zbin1, self.nzbins):

                fname = os.path.join(self.data_directory, 'DATA_VECTOR/KV450_xi_pm_files/KV450_xi_pm_tomo_{:}_{:}_logbin_mcor.dat'.format(zbin1 + 1, zbin2 + 1))
                theta, xip, xim = np.loadtxt(fname, unpack=True)

                # this assumes theta is the same for every tomographic bin and
                # for both xi_p and xi_m!
                if idx_corr == 0:
                    data_xip[:, 0] = theta
                    data_xim[:, 0] = theta

                data_xip[:, idx_corr + 1] = xip
                data_xim[:, idx_corr + 1] = xim

                idx_corr += 1

        data = np.concatenate((data_xip, data_xim))

        print('Loaded data vectors from: \n', os.path.join(self.data_directory, 'DATA_VECTOR/KV450_xi_pm_files/'), '\n')

        return data


    def __load_public_theory_vector(self):
        """
        Helper function to read in the full data vector from public KiDS+VIKING-450
        release and to bring it into the input format used in the original
        CFHTLenS likelihood.
        """
        # plus one for theta-column
        data_xip = np.zeros((self.ntheta, self.nzcorrs + 1))
        data_xim = np.zeros((self.ntheta, self.nzcorrs + 1))
        idx_corr = 0
        for zbin1 in range(self.nzbins):
            for zbin2 in range(zbin1, self.nzbins):

                fname = os.path.join(self.data_directory, 'SUPPLEMENTARY_FILES/THEORY_for_COV_MAT_xi_pm_files/THEORY_for_COV_MAT_xi_pm_tomo_{:}_{:}_logbin.dat'.format(zbin1 + 1, zbin2 + 1))
                theta, xip, xim = np.loadtxt(fname, unpack=True)

                # this assumes theta is the same for every tomographic bin and
                # for both xi_p and xi_m!
                if idx_corr == 0:
                    data_xip[:, 0] = theta
                    data_xim[:, 0] = theta

                data_xip[:, idx_corr + 1] = xip
                data_xim[:, idx_corr + 1] = xim

                idx_corr += 1

        data = np.concatenate((data_xip, data_xim))

        return data


    def __load_public_cov_mat(self):
        """
        Helper function to read in the full covariance matrix from the public
        KiDS+VIKING-450 release and to bring it into format of self.xi_obs.
        """

        try:
            fname = os.path.join(self.data_directory, 'FOR_MONTE_PYTHON/Cov_mat_all_scales_inc_m_use_with_kv450_cf_likelihood_public.dat')
            matrix = np.loadtxt(fname)
            print('Loaded covariance matrix (incl. shear calibration uncertainty) in a format usable with this likelihood from: \n', fname, '\n')

        except:
            fname = os.path.join(self.data_directory, 'COV_MAT/Cov_mat_all_scales.txt')
            tmp_raw = np.loadtxt(fname)

            print('Loaded covariance matrix in list format from: \n', fname)
            print('Now we construct the covariance matrix in a format usable with this likelihood for the first time. \n This might take a few minutes, but only once! \n')

            thetas_plus = self.theta_bins[:self.ntheta]
            thetas_minus = self.theta_bins[self.ntheta:]

            indices = np.column_stack((tmp_raw[:, :3], tmp_raw[:, 4:7]))

            # we need to add both components for full covariance
            values = tmp_raw[:, 8] + tmp_raw[:, 9]

            for i in range(len(tmp_raw)):
                for j in range(self.ntheta):
                    if np.abs(tmp_raw[i, 3] - thetas_plus[j]) <= tmp_raw[i, 3] / 10.:
                        tmp_raw[i, 3] = j
                    if np.abs(tmp_raw[i, 7] - thetas_plus[j]) <= tmp_raw[i, 7] / 10.:
                        tmp_raw[i, 7] = j

            thetas_raw_plus = tmp_raw[:, 3].astype(np.int16)
            thetas_raw_minus = tmp_raw[:, 7].astype(np.int16)

            dim = 2 * self.ntheta * self.nzcorrs
            matrix = np.zeros((dim, dim))

            # ugly brute-force...
            index1 = 0
            # this creates the correctly ordered (i.e. like self.xi_obs) full
            # 180 x 180 covariance matrix:
            for iz1 in range(self.nzbins):
                for iz2 in range(iz1, self.nzbins):
                    for ipm in range(2):
                        for ith in range(self.ntheta):

                            index2 = 0
                            for iz3 in range(self.nzbins):
                                for iz4 in range(iz3, self.nzbins):
                                    for ipm2 in range(2):
                                        for ith2 in range(self.ntheta):
                                            for index_lin in range(len(tmp_raw)):
                                                #print index1, index2
                                                #print iz1, iz2, ipm, ith, iz3, iz4, ipm2
                                                if iz1 + 1 == indices[index_lin, 0] and iz2 + 1 == indices[index_lin, 1] and ipm == indices[index_lin, 2] and iz3 + 1 == indices[index_lin, 3]  and iz4 + 1 == indices[index_lin, 4] and ipm2 == indices[index_lin, 5] and ith == thetas_raw_plus[index_lin] and ith2 == thetas_raw_minus[index_lin]:
                                                    #print 'hit'
                                                    matrix[index1, index2] = values[index_lin]
                                                    matrix[index2, index1] = matrix[index1, index2]
                                            index2 += 1
                            index1 += 1

            # apply propagation of m-correction uncertainty following
            # equation 12 from Hildebrandt et al. 2017 (arXiv:1606.05338):
            err_multiplicative_bias = 0.02
            temp = self.__load_public_theory_vector()
            # rearrange vector into 'observed' sorting:
            xi_theo = self.__get_xi_obs(temp[:, 1:])
            matrix_m_corr = np.matrix(xi_theo).T * np.matrix(xi_theo) * 4. * err_multiplicative_bias**2
            matrix = matrix + np.asarray(matrix_m_corr)

            fname = fname = os.path.join(self.data_directory, 'FOR_MONTE_PYTHON/Cov_mat_all_scales_inc_m_use_with_kv450_cf_likelihood_public.dat')
            if not os.path.isfile(fname):
                np.savetxt(fname, matrix)
                print('Saved covariance matrix (incl. shear calibration uncertainty) in format usable with this likelihood to: \n', fname, '\n')

        return matrix


    def __write_out_vector_in_list_format(self, vec, fname_prefix='your_filename_prefix_here'):

        # Here, we construct the fiducial scale data vector and write it out in
        # list-format with detailed indices:
        # first we construct all necessary index-arrays:
        idx_corr = 0
        for idx_z1 in range(self.nzbins):
            for idx_z2 in range(idx_z1, self.nzbins):

                if idx_corr == 0:
                    thetas_all = self.theta_bins
                    idx_pm = np.concatenate((np.ones(self.ntheta), np.ones(self.ntheta) + 1))
                    idx_tomo_z1 = np.ones(2 * self.ntheta) * (idx_z1 + 1)
                    idx_tomo_z2 = np.ones(2 * self.ntheta) * (idx_z2 + 1)
                else:
                    thetas_all = np.concatenate((thetas_all, self.theta_bins))
                    idx_pm = np.concatenate((idx_pm, np.concatenate((np.ones(self.ntheta), np.ones(self.ntheta) + 1))))
                    idx_tomo_z1 = np.concatenate((idx_tomo_z1, np.ones(2 * self.ntheta) * (idx_z1 + 1)))
                    idx_tomo_z2 = np.concatenate((idx_tomo_z2, np.ones(2 * self.ntheta) * (idx_z2 + 1)))

                idx_corr += 1

        # now apply correct masking:
        thetas_all = thetas_all[self.mask_indices]
        idx_pm = idx_pm[self.mask_indices]
        idx_tomo_z1 = idx_tomo_z1[self.mask_indices]
        idx_tomo_z2 = idx_tomo_z2[self.mask_indices]

        idx_run = np.arange(len(idx_pm)) + 1
        savedata = np.column_stack((idx_run, thetas_all, vec[self.mask_indices], idx_pm, idx_tomo_z1, idx_tomo_z2))
        header = ' i    theta(i)\'        xi_p/m(i)  (p=1, m=2)  itomo   jtomo'
        fname = os.path.join(self.data_directory , 'FOR_MONTE_PYTHON/{:}_cut_to_{:}.dat'.format(fname_prefix, self.mask_suffix))
        np.savetxt(fname, savedata, header=header, delimiter='\t', fmt=['%4i', '%.5e', '%12.5e', '%i', '%i', '%i'])
        print('Saved vector in list format cut down to scales as specified in {:}: \n'.format(self.cutvalues_file), fname, '\n')

        return


    def __get_mask(self, cut_values):

        mask = np.zeros(2 * self.nzcorrs * self.ntheta)
        iz = 0
        for izl in range(self.nzbins):
            for izh in range(izl, self.nzbins):
                # this counts the bin combinations
                # iz=1 =>(1,1), iz=2 =>(1,2) etc
                iz = iz + 1
                for i in range(self.ntheta):
                    j = (iz-1)*2*self.ntheta
                    #xi_plus_cut = max(cut_values[izl, 0], cut_values[izh, 0])
                    xi_plus_cut_low = max(cut_values[izl, 0], cut_values[izh, 0])
                    xi_plus_cut_high = max(cut_values[izl, 1], cut_values[izh, 1])
                    #xi_minus_cut = max(cut_values[izl, 1], cut_values[izh, 1])
                    xi_minus_cut_low = max(cut_values[izl, 2], cut_values[izh, 2])
                    xi_minus_cut_high = max(cut_values[izl, 3], cut_values[izh, 3])
                    if ((self.theta_bins[i] < xi_plus_cut_high) and (self.theta_bins[i]>xi_plus_cut_low)):
                        mask[j+i] = 1
                    if ((self.theta_bins[i] < xi_minus_cut_high) and (self.theta_bins[i]>xi_minus_cut_low)):
                        mask[self.ntheta + j+i] = 1

        return mask

    def __get_xi_obs(self, temp):
        """
        This function takes xi_pm as read in from the data file and constructs
        the xi_pm vector in its observed ordering:
         xi_obs = {xi_p(theta1, z1xz1)... xi_p(thetaK, z1xz1), xi_m(theta1, z1xz1)...
                   xi_m(thetaK, z1xz1);... xi_p(theta1, zNxzN)... xi_p(thetaK, zNxzN),
                   xi_m(theta1, zNxzN)... xi_m(thetaK, zNxN)}
        """

        xi_obs = np.zeros(self.ntheta * self.nzcorrs * 2)

        # create the data-vector:
        k = 0
        for j in range(self.nzcorrs):
            for i in range(2 * self.ntheta):
                xi_obs[k] = temp[i, j]
                k += 1

        return xi_obs

    def __get_xi_p_and_xi_m(self, vec_old):
        """
        This function takes a xi_pm vector in the observed ordering (as it
        comes out of the __get_xi_obs-function for example) and splits it again
        in its xi_p and xi_m parts.
        """

        '''
        tmp = np.zeros((2 * self.ntheta, self.nzbins, self.nzbins), 'float64')
        vec1_new = np.zeros((self.ntheta, self.nzbins, self.nzbins), 'float64')
        vec2_new = np.zeros((self.ntheta, self.nzbins, self.nzbins), 'float64')

        index_corr = 0
        for index_zbin1 in range(self.nzbins):
            for index_zbin2 in range(index_zbin1, self.nzbins):
                #for index_theta in range(ntheta):
                index_low = 2 * self.ntheta * index_corr
                index_high = 2 * self.ntheta * index_corr + 2 * self.ntheta
                #print index_low, index_high
                tmp[:, index_zbin1, index_zbin2] = vec_old[index_low:index_high]
                vec1_new[:, index_zbin1, index_zbin2] = tmp[:self.ntheta, index_zbin1, index_zbin2]
                vec2_new[:, index_zbin1, index_zbin2] = tmp[self.ntheta:, index_zbin1, index_zbin2]

                index_corr += 1
        '''

        tmp = np.zeros((2 * self.ntheta, self.nzcorrs), 'float64')
        vec1_new = np.zeros((self.ntheta, self.nzcorrs), 'float64')
        vec2_new = np.zeros((self.ntheta, self.nzcorrs), 'float64')

        for index_corr in range(self.nzcorrs):
            index_low = 2 * self.ntheta * index_corr
            index_high = 2 * self.ntheta * index_corr + 2 * self.ntheta
            #print index_low, index_high
            tmp[:, index_corr] = vec_old[index_low:index_high]
            vec1_new[:, index_corr] = tmp[:self.ntheta, index_corr]
            vec2_new[:, index_corr] = tmp[self.ntheta:, index_corr]

        return vec1_new, vec2_new

    def baryon_feedback_bias_sqr(self, k, z, A_bary=1.):
        """

        Fitting formula for baryon feedback after equation 10 and Table 2 from J. Harnois-Deraps et al. 2014 (arXiv.1407.4301)

        """

        # k is expected in h/Mpc and is divided in log by this unit...
        x = np.log10(k)

        a = 1. / (1. + z)
        a_sqr = a * a

        constant = {'AGN':   {'A2': -0.11900, 'B2':  0.1300, 'C2':  0.6000, 'D2':  0.002110, 'E2': -2.0600,
                              'A1':  0.30800, 'B1': -0.6600, 'C1': -0.7600, 'D1': -0.002950, 'E1':  1.8400,
                              'A0':  0.15000, 'B0':  1.2200, 'C0':  1.3800, 'D0':  0.001300, 'E0':  3.5700},
                    'REF':   {'A2': -0.05880, 'B2': -0.2510, 'C2': -0.9340, 'D2': -0.004540, 'E2':  0.8580,
                              'A1':  0.07280, 'B1':  0.0381, 'C1':  1.0600, 'D1':  0.006520, 'E1': -1.7900,
                              'A0':  0.00972, 'B0':  1.1200, 'C0':  0.7500, 'D0': -0.000196, 'E0':  4.5400},
                    'DBLIM': {'A2': -0.29500, 'B2': -0.9890, 'C2': -0.0143, 'D2':  0.001990, 'E2': -0.8250,
                              'A1':  0.49000, 'B1':  0.6420, 'C1': -0.0594, 'D1': -0.002350, 'E1': -0.0611,
                              'A0': -0.01660, 'B0':  1.0500, 'C0':  1.3000, 'D0':  0.001200, 'E0':  4.4800}}

        A_z = constant[self.baryon_model]['A2']*a_sqr+constant[self.baryon_model]['A1']*a+constant[self.baryon_model]['A0']
        B_z = constant[self.baryon_model]['B2']*a_sqr+constant[self.baryon_model]['B1']*a+constant[self.baryon_model]['B0']
        C_z = constant[self.baryon_model]['C2']*a_sqr+constant[self.baryon_model]['C1']*a+constant[self.baryon_model]['C0']
        D_z = constant[self.baryon_model]['D2']*a_sqr+constant[self.baryon_model]['D1']*a+constant[self.baryon_model]['D0']
        E_z = constant[self.baryon_model]['E2']*a_sqr+constant[self.baryon_model]['E1']*a+constant[self.baryon_model]['E0']

        # only for debugging; tested and works!
        #print 'AGN: A2=-0.11900, B2= 0.1300, C2= 0.6000, D2= 0.002110, E2=-2.0600'
        #print self.baryon_model+': A2={:.5f}, B2={:.5f}, C2={:.5f}, D2={:.5f}, E2={:.5f}'.format(constant[self.baryon_model]['A2'], constant[self.baryon_model]['B2'], constant[self.baryon_model]['C2'],constant[self.baryon_model]['D2'], constant[self.baryon_model]['E2'])

        # original formula:
        #bias_sqr = 1.-A_z*np.exp((B_z-C_z)**3)+D_z*x*np.exp(E_z*x)
        # original formula with a free amplitude A_bary:
        bias_sqr = 1. - A_bary * (A_z * np.exp((B_z * x - C_z)**3) - D_z * x * np.exp(E_z * x))

        return bias_sqr

    def get_IA_factor(self, z, linear_growth_rate, amplitude, exponent):

        const = 5e-14 / self.small_h**2 # Mpc^3 / M_sol

        # arbitrary convention
        z0 = 0.3
        #print utils.growth_factor(z, self.Omega_m)
        #print self.rho_crit
        factor = -1. * amplitude * const * self.rho_crit * self.Omega_m / linear_growth_rate * ((1. + z) / (1. + z0))**exponent

        return factor

    def get_critical_density(self):
        """
        The critical density of the Universe at redshift 0.

        Returns
        -------
        rho_crit in solar masses per cubic Megaparsec.

        """

        # yay, constants...
        Mpc_cm = 3.08568025e24 # cm
        M_sun_g = 1.98892e33 # g
        G_const_Mpc_Msun_s = M_sun_g * (6.673e-8) / Mpc_cm**3.
        H100_s = 100. / (Mpc_cm * 1.0e-5) # s^-1

        rho_crit_0 = 3. * (self.small_h * H100_s)**2. / (8. * np.pi * G_const_Mpc_Msun_s)

        return rho_crit_0


    def loglkl(self, cosmo, data):
        # These arrays will be filled with the xi's for the final z-bins by summing up the contributions of comb components, weighted by the amplitudes
        self.xi1_finalbins = np.zeros((self.nthetatot, self.nzcorrs), 'float64')
        self.xi2_finalbins = np.zeros((self.nthetatot, self.nzcorrs), 'float64')
        # Vector of derivative of xi prediction with respect to the calibration parameters
        self.xi1_prime = np.zeros((self.nzbins*self.ngaussians, self.nthetatot, self.nzcorrs), 'float64')
        self.xi2_prime = np.zeros((self.nzbins*self.ngaussians, self.nthetatot, self.nzcorrs), 'float64')
        # Matrix of derivative of xi prediction with respect to the calibration parameters
        self.xi1_2prime = np.zeros((self.nzbins*self.ngaussians, self.nzbins*self.ngaussians, self.nthetatot, self.nzcorrs), 'float64')
        self.xi2_2prime = np.zeros((self.nzbins*self.ngaussians, self.nzbins*self.ngaussians, self.nthetatot, self.nzcorrs), 'float64')
        # Same as previous arrays, but for splined xi's
        self.xi1_prime_theta = np.empty((self.nzbins*self.ngaussians,self.nzcorrs), dtype=(list, 3))
        self.xi2_prime_theta = np.empty((self.nzbins*self.ngaussians,self.nzcorrs), dtype=(list, 3))
        self.xi1_2prime_theta = np.empty((self.nzbins*self.ngaussians,self.nzbins*self.ngaussians,self.nzcorrs), dtype=(list, 3))
        self.xi2_2prime_theta = np.empty((self.nzbins*self.ngaussians,self.nzbins*self.ngaussians,self.nzcorrs), dtype=(list, 3))
        # Vector of derivative of xi prediction (xi+ and xi- combined)
        self.xi_prime = np.zeros((self.nzbins*self.ngaussians,np.size(self.xi_obs)), 'float64')
        # Matrix of derivative of xi prediction (xi+ and xi- combined)
        self.xi_2prime = np.zeros((self.nzbins*self.ngaussians,self.nzbins*self.ngaussians,np.size(self.xi_obs)), 'float64')
        #  Vector of derivative of the likelihood with respect to the calibration parameters
        self.L_prime = np.zeros((self.nzbins*self.ngaussians), 'float64')
        #  Matrix of derivative of the likelihood with respect to the calibration parameters
        self.L_2prime = np.zeros((self.nzbins*self.ngaussians,self.nzbins*self.ngaussians), 'float64')
        # Omega_m contains all species!
        self.Omega_m = cosmo.Omega_m()
        self.small_h = cosmo.h()
        # needed for IA modelling:
        if ('A_IA' in data.mcmc_parameters) and ('exp_IA' in data.mcmc_parameters):
            amp_IA = data.mcmc_parameters['A_IA']['current'] * data.mcmc_parameters['A_IA']['scale']
            exp_IA = data.mcmc_parameters['exp_IA']['current'] * data.mcmc_parameters['exp_IA']['scale']
            intrinsic_alignment = True
        elif ('A_IA' in data.mcmc_parameters) and ('exp_IA' not in data.mcmc_parameters):
            amp_IA = data.mcmc_parameters['A_IA']['current'] * data.mcmc_parameters['A_IA']['scale']
            # redshift-scaling is turned off:
            exp_IA = 0.

            intrinsic_alignment = True
        else:
            intrinsic_alignment = False

        # One wants to obtain here the relation between z and r, this is done
        # by asking the cosmological module with the function z_of_r
        self.r, self.dzdr = cosmo.z_of_r(self.z_p)

        # Compute now the selection function p(r) = p(z) dz/dr normalized
        # to one. The np.newaxis helps to broadcast the one-dimensional array
        # dzdr to the proper shape. Note that p_norm is also broadcasted as
        # an array of the same shape as p_z

        # simply calculate p(r)
        self.pr = self.p_i * (self.dzdr[:, np.newaxis])
        # nuisance parameter for m-correction (one value for all bins):
        # implemented tomography-friendly so it's very easy to implement a dm per z-bin from here!
        param_name = 'dm'
        if param_name in data.mcmc_parameters:

            dm_per_zbin = np.ones((self.ntheta, self.nzbins))
            dm_per_zbin *= data.mcmc_parameters[param_name]['current'] * data.mcmc_parameters[param_name]['scale']

        else:
            # so that nothing will change if we don't marginalize over dm!
            dm_per_zbin = np.zeros((self.ntheta, self.nzbins))

        # nuisance parameters for constant c-correction:
        dc1_per_zbin = np.zeros((self.ntheta, self.nzbins))
        dc2_per_zbin = np.zeros((self.ntheta, self.nzbins))
        for zbin in range(self.nzbins):

            #param_name = 'dc_z{:}'.format(zbin + 1)
            param_name = 'dc'

            if param_name in data.mcmc_parameters:
                dc1_per_zbin[:, zbin] = np.ones(self.ntheta) * data.mcmc_parameters[param_name]['current'] * data.mcmc_parameters[param_name]['scale']
                # add here dc2 if xi- turns out to be affected!
                #dc2_per_zbin[zbin] = dc2_per_zbin[zbin]

        # correlate dc1/2_per_zbin in tomographic order of xi1/2:
        dc1_sqr = np.zeros((self.ntheta, self.nzcorrs))
        dc2_sqr = np.zeros((self.ntheta, self.nzcorrs))
        # correlate dm_per_zbin in tomographic order of xi1/2:
        dm_plus_one_sqr = np.zeros((self.ntheta, self.nzcorrs))
        index_corr = 0
        for zbin1 in range(self.nzbins):
            for zbin2 in range(zbin1, self.nzbins):

                # c-correction:
                dc1_sqr[:, index_corr] = dc1_per_zbin[:, zbin1] * dc1_per_zbin[:, zbin2]
                dc2_sqr[:, index_corr] = dc2_per_zbin[:, zbin1] * dc2_per_zbin[:, zbin2]

                # m-correction:
                dm_plus_one_sqr[:, index_corr] = (1. + dm_per_zbin[:, zbin1]) * (1. + dm_per_zbin[:, zbin2])

                index_corr += 1

        # get c-correction into form of xi_obs
        temp = np.concatenate((dc1_sqr, dc2_sqr))
        dc_sqr = self.__get_xi_obs(temp)

        # get m-correction into form of xi_obs
        temp = np.concatenate((dm_plus_one_sqr, dm_plus_one_sqr))
        dm_plus_one_sqr_obs = self.__get_xi_obs(temp)

        # Below we construct a theta-dependent c-correction function from
        # measured data (for one z-bin) and scale it with an amplitude per z-bin
        # which is to be fitted
        # this is all independent of the constant c-correction calculated above

        xip_c = np.zeros((self.ntheta, self.nzcorrs))
        xim_c = np.zeros((self.ntheta, self.nzcorrs))
        if self.use_cterm_function:

            amps_cfunc = np.ones(self.nzbins)
            for zbin in range(self.nzbins):

                #param_name = 'Ac_z{:}'.format(zbin + 1)
                param_name = 'Ac'

                if param_name in data.mcmc_parameters:
                    amps_cfunc[zbin] = data.mcmc_parameters[param_name]['current'] * data.mcmc_parameters[param_name]['scale']

            index_corr = 0
            for zbin1 in range(self.nzbins):
                for zbin2 in range(zbin1, self.nzbins):
                    #sign = np.sign(amps_cfunc[zbin1]) * np.sign(amps_cfunc[zbin2])
                    #xip_c[:, index_corr] = sign * np.sqrt(np.abs(amps_cfunc[zbin1] * amps_cfunc[zbin2])) * self.xip_c_per_zbin
                    xip_c[:, index_corr] = amps_cfunc[zbin1] * amps_cfunc[zbin2] * self.xip_c_per_zbin
                    # TODO: we leave xim_c set to 0 for now!
                    #xim_c[:, index_corr] = amps_cfunc[zbin1] * amps_cfunc[zbin2] * self.xim_c_per_zbin

                    index_corr += 1

        # get it into order of xi_obs
        # contains only zeros if function is not requested
        # TODO xim-component contains only zeros
        temp = np.concatenate((xip_c, xim_c))
        xipm_c = self.__get_xi_obs(temp)

        # get linear growth rate if IA are modelled:
        if intrinsic_alignment:
            self.rho_crit = self.get_critical_density()
            # derive the linear growth factor D(z)
            linear_growth_rate = np.zeros_like(self.z_p)
            #print self.redshifts
            for index_z, z in enumerate(self.z_p):
                linear_growth_rate[index_z] = cosmo.scale_independent_growth_factor(z)
            # normalize to unity at z=0:
            linear_growth_rate /= cosmo.scale_independent_growth_factor(0.)

        # Compute function g_i(r), that depends on r and the bin
        # g_i(r) = 2r(1+z(r)) int_r^+\infty drs p_r(rs) (rs-r)/rs
        for Bin in range(self.ngaussians):
            # shift from first entry only useful if first enrty is 0!
        # !!! KiDS450 uses range(self.nzmax - 1)!!! not sure what is right!
            # for nr in range(1, self.nzmax-1):
            for nr in range(self.nzmax - 1):
                fun = self.pr[nr:, Bin] * (self.r[nr:] - self.r[nr]) / self.r[nr:]
                self.g[nr, Bin] = np.sum(0.5 * (fun[1:] + fun[:-1]) * (self.r[nr + 1:] - self.r[nr:-1]))
                self.g[nr, Bin] *= 2. * self.r[nr] * (1. + self.z_p[nr])
        #print 'g(r) \n', self.g

        # Get power spectrum P(k=l/r,z(r)) from cosmological module
        #self.pk_dm = np.zeros_like(self.pk)
        k_save = np.zeros(self.nlmax)
        kmax_in_inv_Mpc = self.k_max_h_by_Mpc * cosmo.h()
        for index_l in range(self.nlmax):
            for index_z in range(1, self.nzmax):

                k_in_inv_Mpc = (self.l[index_l] + 0.5) / self.r[index_z]
                k_save[index_l] = k_in_inv_Mpc
                if (k_in_inv_Mpc > kmax_in_inv_Mpc):
                    pk_dm = 0.
                    pk_lin_dm = 0.
                else:
                    pk_dm = cosmo.pk(k_in_inv_Mpc, self.z_p[index_z])
                    pk_lin_dm = cosmo.pk_lin(k_in_inv_Mpc, self.z_p[index_z])

                if 'A_bary' in data.mcmc_parameters:
                    A_bary = data.mcmc_parameters['A_bary']['current'] * data.mcmc_parameters['A_bary']['scale']
                    self.pk[index_l, index_z] = pk_dm * self.baryon_feedback_bias_sqr(k_in_inv_Mpc / self.small_h, self.z_p[index_z], A_bary=A_bary)
                    # don't apply the baryon feedback model to the linear Pk!
                    #self.pk_lin[index_l, index_z] = pk_lin_dm * self.baryon_feedback_bias_sqr(k_in_inv_Mpc / self.small_h, self.z_p[index_z], A_bary=A_bary)
                else:
                    self.pk[index_l, index_z] = pk_dm
                    self.pk_lin[index_l, index_z] = pk_lin_dm

        # Save out P(k, z)
        # fname = os.path.join(self.data_directory, 'pk.dat')
        # np.savetxt(fname, np.column_stack((k_save, self.pk)))
        # print 'Saved P(k, z) to: \n', fname

        '''
        # Recover the non_linear scale computed by halofit. If no scale was
        # affected, set the scale to one, and make sure that the nuisance
        # parameter epsilon is set to zero
        if (cosmo.nonlinear_method == 0):
            self.k_sigma[:] = 1.e6
        else:
            self.k_sigma = cosmo.nonlinear_scale(self.z_p, self.nzmax)

        # Define the alpha function, that will characterize the theoretical
        # uncertainty. Chosen to be 0.001 at low k, raise between 0.1 and 0.2
        # to self.theoretical_error
        if self.theoretical_error != 0:
            for index_l in range(self.nlmax):
                k = (self.l[index_l] + 0.5) / self.r[1:]
                self.alpha[index_l, 1:] = np.log(1. + k[1:] / self.k_sigma[1:]) / (1. + np.log(1. + k[1:] / self.k_sigma[1:])) * self.theoretical_error

        # recover the e_th_nu part of the error function
        e_th_nu = self.coefficient_f_nu * cosmo.Omega_nu / cosmo.Omega_m()

        # Compute the Error E_th_nu function
        if 'epsilon' in self.use_nuisance:
            for index_l in range(self.nlmax):
                self.E_th_nu[index_l, 1:] = np.log(1. + self.l[index_l] / self.k_sigma[1:] * self.r[1:]) / (1. + np.log(1. + self.l[index_l] / self.k_sigma[1:] * self.r[1:])) * e_th_nu

        # Add the error function, with the nuisance parameter, to P_nl_th, if
        # the nuisance parameter exists
                for index_l in range(self.nlmax):
                    epsilon = data.mcmc_parameters['epsilon']['current'] * (data.mcmc_parameters['epsilon']['scale'])
                    self.pk[index_l, 1:] *= (1. + epsilon * self.E_th_nu[index_l, 1:])
        '''

        Cl_GG_integrand = np.zeros_like(self.Cl_integrand)
        Cl_GG = np.zeros_like(self.Cl)

        if intrinsic_alignment:
            Cl_II_integrand = np.zeros_like(self.Cl_integrand)
            Cl_II = np.zeros_like(self.Cl)

            Cl_GI_integrand = np.zeros_like(self.Cl_integrand)
            Cl_GI = np.zeros_like(self.Cl)

        dr = self.r[1:] - self.r[:-1]
        # Start loop over l for computation of C_l^shear
        # Start loop over l for computation of E_l
        for il in range(self.nlmax):
            # find Cl_integrand = (g(r) / r)**2 * P(l/r,z(r))
            for Bin1 in range(self.ngaussians):
                for Bin2 in range(Bin1, self.ngaussians):
                    Cl_GG_integrand[1:, self.one_dim_index(Bin1,Bin2,self.ngaussians)] = self.g[1:, Bin1] * self.g[1:, Bin2] / self.r[1:]**2 * self.pk[il, 1:]
                    #print self.Cl_integrand
                    if intrinsic_alignment:
                        factor_IA = self.get_IA_factor(self.z_p, linear_growth_rate, amp_IA, exp_IA)
                        #print self.eta_r[1:, zbin1].shape

                        if self.use_linear_pk_for_IA:
                            # this term (II) uses the linear matter power spectrum P_lin(k, z)
                            Cl_II_integrand[1:, self.one_dim_index(Bin1,Bin2,self.ngaussians)] = self.pr[1:, Bin1] * self.pr[1:, Bin2] * factor_IA[1:]**2 / self.r[1:]**2 * self.pk_lin[il, 1:]
                            # this term (GI) uses sqrt(P_lin(k, z) * P_nl(k, z))
                            Cl_GI_integrand[1:, self.one_dim_index(Bin1,Bin2,self.ngaussians)] = (self.g[1:, Bin1] * self.pr[1:, Bin2] + self.g[1:, Bin2] * self.pr[1:, Bin1]) * factor_IA[1:] / self.r[1:]**2 * np.sqrt(self.pk_lin[il, 1:] * self.pk[il, 1:])
                        else:
                            # both II and GI terms use the non-linear matter power spectrum P_nl(k, z)
                            Cl_II_integrand[1:, self.one_dim_index(Bin1,Bin2,self.ngaussians)] = self.pr[1:, Bin1] * self.pr[1:, Bin2] * factor_IA[1:]**2 / self.r[1:]**2 * self.pk[il, 1:]
                            Cl_GI_integrand[1:, self.one_dim_index(Bin1,Bin2,self.ngaussians)] = (self.g[1:, Bin1] * self.pr[1:, Bin2] + self.g[1:, Bin2] * self.pr[1:, Bin1]) * factor_IA[1:] / self.r[1:]**2 * self.pk[il, 1:]

                    '''
                    if self.theoretical_error != 0:
                        self.El_integmrand[1:, self.one_dim_index(Bin1, Bin2)] = self.g[1:, Bin1] * self.g[1:, Bin2] / self.r[1:]**2 * self.pk[il, 1:] * self.alpha[il, 1:]
                    '''

            # Integrate over r to get C_l^shear_ij = P_ij(l)
            # C_l^shear_ij = 9/16 Omega0_m^2 H_0^4 \sum_0^rmax dr (g_i(r)
            # g_j(r) /r**2) P(k=l/r,z(r)) dr
            # It is then multiplied by 9/16*Omega_m**2
            # and then by (h/2997.9)**4 to be dimensionless
            # (since P(k)*dr is in units of Mpc**4)
            for Bin in range(self.nzcorrs_gaussians):
                Cl_GG[il, Bin] = np.sum(0.5 * (Cl_GG_integrand[1:, Bin] + Cl_GG_integrand[:-1, Bin]) * dr)
                Cl_GG[il, Bin] *= 9. / 16. * self.Omega_m**2
                Cl_GG[il, Bin] *= (self.small_h / 2997.9)**4

                if intrinsic_alignment:
                    Cl_II[il, Bin] = np.sum(0.5 * (Cl_II_integrand[1:, Bin] + Cl_II_integrand[:-1, Bin]) * dr)

                    Cl_GI[il, Bin] = np.sum(0.5 * (Cl_GI_integrand[1:, Bin] + Cl_GI_integrand[:-1, Bin]) * dr)
                    # here we divide by 4, because we get a 2 from g(r)!
                    Cl_GI[il, Bin] *= 3. / 4. * self.Omega_m
                    Cl_GI[il, Bin] *= (self.small_h / 2997.9)**2
                '''
                if self.theoretical_error != 0:
                    self.El[il, Bin] = np.sum(0.5 * (self.El_integrand[1:, Bin] + self.El_integrand[:-1, Bin]) * dr)
                    self.El[il, Bin] *= 9. / 16. * self.Omega_m**2
                    self.El[il, Bin] *= (self.small_h / 2997.9)**4
                '''
            '''
            for Bin1 in range(self.nzbins):
                Cl_GG[il, self.one_dim_index(Bin1, Bin1)] += self.noise
            '''

        if intrinsic_alignment:
            self.Cl = Cl_GG + Cl_GI + Cl_II
        else:
            self.Cl = Cl_GG

        # Spline Cl[il,Bin1,Bin2] along l
        for Bin in range(self.nzcorrs_gaussians):
            self.spline_Cl[Bin] = list(itp.splrep(self.l, self.Cl[:, Bin]))

        # Interpolate Cl at values lll and store results in Cll
        for Bin in range(self.nzcorrs_gaussians):
            self.Cll[Bin,:] = itp.splev(self.lll[:], self.spline_Cl[Bin])

        if self.integrate_Bessel_with == 'brute_force':
            # this seems to produce closest match in comparison with CCL
            # I still don't like the approach of just integrating the Bessel
            # functions over some predefined multipole range...
            #t0 = timer()
            # Start loop over theta values
            for it in range(self.nthetatot):
                #ilmax = self.il_max[it]
                self.BBessel0[:] = special.j0(self.lll[:] * self.theta[it] * self.a2r)
                self.BBessel4[:] = special.jv(4, self.lll[:] * self.theta[it] * self.a2r)

                # Here is the actual trapezoidal integral giving the xi's:
                # - in more explicit style:
                # for Bin in range(self.nzbin_pairs):
                #     for il in range(ilmax):
                #         self.xi1[it, Bin] = np.sum(self.ldl[il]*self.Cll[Bin,il]*self.BBessel0[il])
                #         self.xi2[it, Bin] = np.sum(self.ldl[il]*self.Cll[Bin,il]*self.BBessel4[il])
                # - in more compact and vectorizable style:
                self.xi1[it, :] = np.sum(self.ldl[:] * self.Cll[:, :] * self.BBessel0[:], axis=1)
                self.xi2[it, :] = np.sum(self.ldl[:] * self.Cll[:, :] * self.BBessel4[:], axis=1)
            #dt = timer() - t0
            #print 'dt = {:.6f}'.format(dt)
            #print self.lll.min(), self.lll.max(), self.lll.shape
            #exit()
            # normalize xis
            self.xi1 = self.xi1 / (2. * math.pi)
            self.xi2 = self.xi2 / (2. * math.pi)

        elif self.integrate_Bessel_with == 'fftlog':
            #t0 = timer()
            #for it in range(self.nthetatot):
            for zcorr in range(self.nzcorrs_gaussians):

                # convert theta from arcmin to deg; xis are already normalized!
                self.xi1[:, zcorr] = self.Cl2xi(self.Cll[zcorr, :], self.lll[:], self.theta[:] / 60., bessel_order=0) #, ell_min_fftlog=self.lll.min(), ell_max_fftlog=self.lll.max() + 1e4)
                self.xi2[:, zcorr] = self.Cl2xi(self.Cll[zcorr, :], self.lll[:], self.theta[:] / 60., bessel_order=4) #, ell_min_fftlog=self.lll.min(), ell_max_fftlog=self.lll.max() + 1e4)
            #dt = timer() - t0
            #print 'dt = {:.6f}'.format(dt)
            #print self.lll.min(), self.lll.max(), self.lll.shape
            #exit()
        else:
            #t0 = timer()
            for it in range(self.nthetatot):
                ilmax = self.il_max[it]

                self.BBessel0[:ilmax] = special.j0(self.lll[:ilmax] * self.theta[it] * self.a2r)
                self.BBessel4[:ilmax] = special.jv(4, self.lll[:ilmax] * self.theta[it] * self.a2r)

                # Here is the actual trapezoidal integral giving the xi's:
                # - in more explicit style:
                # for Bin in range(self.nzcorrs):
                #     for il in range(ilmax):
                #         self.xi1[it, Bin] = np.sum(self.ldl[il]*self.Cll[Bin,il]*self.BBessel0[il])
                #         self.xi2[it, Bin] = np.sum(self.ldl[il]*self.Cll[Bin,il]*self.BBessel4[il])
                # - in more compact and vectorizable style:
                self.xi1[it, :] = np.sum(self.ldl[:ilmax] * self.Cll[:, :ilmax] * self.BBessel0[:ilmax], axis=1)
                self.xi2[it, :] = np.sum(self.ldl[:ilmax] * self.Cll[:, :ilmax] * self.BBessel4[:ilmax], axis=1)
            #dt = timer() - t0
            #print 'dt = {:.6f}'.format(dt)
            #print self.lll.min(), self.lll.max(), self.lll.shape
            #exit()
            # normalize xis
            self.xi1 = self.xi1 / (2. * math.pi)
            self.xi2 = self.xi2 / (2. * math.pi)

        # Construct the xi's for the final z-bins by adding the contributions from the comb components, weighted by the amplitude of the calibration
        for Bin1 in range(self.nzbins):
            for Bin2 in range(Bin1,self.nzbins):
                index = self.one_dim_index(Bin1,Bin2,self.nzbins)
                # Both sums sum over all gaussians!
                for gaussian1 in range(self.ngaussians):
                    for gaussian2 in range(self.ngaussians):
                        index_gaussian=self.one_dim_index(gaussian1,gaussian2,self.ngaussians)
                        self.xi1_finalbins[:,index] += self.A[Bin1,gaussian1]*self.A[Bin2,gaussian2]*self.xi1[:,index_gaussian]
                        self.xi2_finalbins[:,index] += self.A[Bin1,gaussian1]*self.A[Bin2,gaussian2]*self.xi2[:,index_gaussian]
        # Construct the xi_prime vector (eq.17))
        # This is a nzbins*ngaussians-dimensional vector because that's the number of redshift calibration parameters
        # Each vector element contains nzbins*(nzbins+1)/2 values for all possible combinations of redshift bins (similar to fiducial xi-vector)
        if self.simple_approximation or self.full_marginalisation:
            # Loop through entries of the vector
            for vector_zbin in range(self.nzbins):
                for vector_component in range(self.ngaussians):
                    # Determine the vector index for a given z-bin and comb-component
                    index = self.one_dim_index_L_vector(vector_zbin,vector_component,self.ngaussians)
                    # Loop through redshift bin combinations
                    for bin1 in range(self.nzbins):
                        for bin2 in range(bin1,self.nzbins):
                            # Determine index of redshift bin combination
                            index_zbins = self.one_dim_index(bin1,bin2,self.nzbins)
                            xi1_sum = np.zeros(self.nthetatot, 'float64')
                            xi2_sum = np.zeros(self.nthetatot, 'float64')
                            for i in range(self.ngaussians):
                                factor = 0.
                                # Delta functions (eq.17)
                                if bin1 == vector_zbin:
                                    factor += self.A[bin2,i]
                                if bin2 == vector_zbin:
                                    factor += self.A[bin1,i]
                                sum_index = self.one_dim_index(i,vector_component,self.ngaussians)
                                xi1_sum += self.xi1[:,sum_index]*factor
                                xi2_sum += self.xi2[:,sum_index]*factor
                            # Put everything together
                            self.xi1_prime[index,:,index_zbins] = (-self.A[vector_zbin,vector_component]*xi1_sum)
                            self.xi2_prime[index,:,index_zbins] = (-self.A[vector_zbin,vector_component]*xi2_sum)

        if self.full_marginalisation:
            for mu in range(self.nzbins):
                for m in range(self.ngaussians):
                    for nu in range(self.nzbins):
                        for n in range(self.ngaussians):
                            # Determine the vector index for a given z-bin and comb-component
                            index1 = self.one_dim_index_L_vector(mu,m,self.ngaussians)
                            index2 = self.one_dim_index_L_vector(nu,n,self.ngaussians)
                            # Loop through redshift bin combinations
                            for alpha in range(self.nzbins):
                                for beta in range(alpha,self.nzbins):
                                    # Determine index of redshift bin combination
                                    index_zbins = self.one_dim_index(alpha,beta,self.nzbins)
                                    xi1_temp = np.zeros(self.nthetatot, 'float64')
                                    xi2_temp = np.zeros(self.nthetatot, 'float64')
                                    factor = 0
                                    if ((alpha == mu) and (beta == nu)):
                                        factor +=1
                                    if ((beta == mu) and (alpha == nu)):
                                        factor +=1
                                    if factor > 0:
                                        index_components = self.one_dim_index(m, n, self.ngaussians)
                                        xi1_temp -= (self.A[mu,m] * self.A[nu,n] * self.xi1[:,index_components] * factor)
                                        xi2_temp -= (self.A[mu,m] * self.A[nu,n] * self.xi2[:,index_components] * factor)
                                    if ((m==n) and (mu==nu)):
                                        xi1_sum = np.zeros(self.nthetatot, 'float64')
                                        xi2_sum = np.zeros(self.nthetatot, 'float64')
                                        for i in range(self.ngaussians):
                                            factor2 = 0
                                            if alpha == mu:
                                                factor2 += (self.A[beta,i])
                                            if beta == mu:
                                                factor2 += (self.A[alpha,i])
                                            if factor2 > 0:
                                                sum_index = self.one_dim_index(i,m,self.ngaussians)
                                                xi1_sum += self.xi1[:,sum_index] * factor2
                                                xi2_sum += self.xi2[:,sum_index] * factor2

                                        xi1_temp -= (self.A[mu,m] * xi1_sum)
                                        xi2_temp -= (self.A[mu,m] * xi2_sum)
                                    # Put everything together
                                    self.xi1_2prime[index1,index2,:,index_zbins] = xi1_temp
                                    self.xi2_2prime[index1,index2,:,index_zbins] = xi2_temp


        # Spline the xi's
        for Bin in range(self.nzcorrs):
            self.xi1_theta[Bin] = list(itp.splrep(self.theta, self.xi1_finalbins[:,Bin]))
            self.xi2_theta[Bin] = list(itp.splrep(self.theta, self.xi2_finalbins[:,Bin]))

        if self.simple_approximation or self.full_marginalisation:
            # Loop through entries of the matrix
            for index in range(self.nfitparameters):
                    for Bin in range(self.nzcorrs):
                        self.xi1_prime_theta[index,Bin] = list(itp.splrep(self.theta, self.xi1_prime[index,:,Bin]))
                        self.xi2_prime_theta[index,Bin] = list(itp.splrep(self.theta, self.xi2_prime[index,:,Bin]))

        if self.full_marginalisation:
            # Loop through entries of the matrix
            for i in range(self.nfitparameters):
                for j in range(self.nfitparameters):
                    for Bin in range(self.nzcorrs):
                        self.xi1_2prime_theta[i,j,Bin] = itp.splrep(self.theta, self.xi1_2prime[i,j,:,Bin])
                        self.xi2_2prime_theta[i,j,Bin] = itp.splrep(self.theta, self.xi2_2prime[i,j,:,Bin])

        # From now on we no longer work with the comb
        # xi_p = np.zeros((self.ntheta, self.nzcorrs))
        # xi_m = np.zeros((self.ntheta, self.nzcorrs))
        if self.use_theory_binning:
            #t0 = timer()
            # roughly 0.01s to 0.02s extra...
            for idx_theta in range(self.ntheta):
                #theta = np.linspace(self.theta_bin_min[idx_theta], self.theta_bin_max[idx_theta], int(self.theta_nodes_theory))
                theta = self.thetas_for_theory_binning[idx_theta, :]
                dtheta = (theta[1:] - theta[:-1]) * self.a2r

                for idx_bin in range(self.nzcorrs):

                    xi_p_integrand = itp.splev(theta, self.xi1_theta[idx_bin]) * itp.splev(theta, self.theory_weight_func)
                    xi_m_integrand = itp.splev(theta, self.xi2_theta[idx_bin]) * itp.splev(theta, self.theory_weight_func)

                    xi_p[idx_theta, idx_bin] = np.sum(0.5 * (xi_p_integrand[1:] + xi_p_integrand[:-1]) * dtheta) / self.int_weight_func[idx_theta]
                    xi_m[idx_theta, idx_bin] = np.sum(0.5 * (xi_m_integrand[1:] + xi_m_integrand[:-1]) * dtheta) / self.int_weight_func[idx_theta]

            # now mix xi_p and xi_m back into xi_obs:
            temp = np.concatenate((xi_p, xi_m))
            self.xi = self.__get_xi_obs(temp)
            #dt = timer() - t0
            # print dt

        else:
            # Get xi's in same column vector format as the data
            #iz = 0
            #for Bin in range(self.nzcorrs):
            #    iz = iz + 1  # this counts the bin combinations
            #    for i in range(self.ntheta):
            #        j = (iz-1)*2*self.ntheta
            #        self.xi[j+i] = itp.splev(
            #            self.theta_bins[i], self.xi1_theta[Bin])
            #        self.xi[self.ntheta + j+i] = itp.splev(
            #            self.theta_bins[i], self.xi2_theta[Bin])
            # or in more compact/vectorizable form:

            ############# For splines
            # iz = 0
            # for Bin in range(self.nzcorrs):
            #     iz = iz + 1  # this counts the bin combinations
            #     j = (iz - 1) * 2 * self.ntheta
            #     self.xi[j:j + self.ntheta] = itp.splev(self.theta_bins[:self.ntheta], self.xi1_theta[Bin])
            #     self.xi[j + self.ntheta:j + 2 * self.ntheta] = itp.splev(self.theta_bins[:self.ntheta], self.xi2_theta[Bin])
            # if self.simple_approximation or self.full_marginalisation:
            #     for index in range(self.nfitparameters):
            #         iz = 0
            #         for Bin in range(self.nzcorrs):
            #             iz = iz + 1  # this counts the bin combinations
            #             j = (iz - 1) * 2 * self.ntheta
            #             self.xi_prime[index,j:j + self.ntheta] = itp.splev(self.theta_bins[:self.ntheta], self.xi1_prime_theta[index,Bin])
            #             self.xi_prime[index,j + self.ntheta:j + 2 * self.ntheta] = itp.splev(self.theta_bins[:self.ntheta], self.xi2_prime_theta[index,Bin])
            # if self.full_marginalisation:
            #     for m in range(self.nfitparameters):
            #         for n in range(self.nfitparameters):
            #             iz = 0
            #             for Bin in range(self.nzcorrs):
            #                 iz = iz + 1  # this counts the bin combinations
            #                 j = (iz - 1) * 2 * self.ntheta
            #                 self.xi_2prime[m,n,j:j + self.ntheta] = itp.splev(self.theta_bins[:self.ntheta], self.xi1_2prime_theta[m,n,Bin])
            #                 self.xi_2prime[m,n,j + self.ntheta:j + 2 * self.ntheta] = itp.splev(self.theta_bins[:self.ntheta], self.xi2_2prime_theta[m,n,Bin])
            ########### For non splines
            iz = 0
            for Bin in range(self.nzcorrs):
                iz = iz + 1  # this counts the bin combinations
                j = (iz - 1) * 2 * self.ntheta
                self.xi[j:j + self.ntheta] = self.xi1_finalbins[:,Bin]
                self.xi[j + self.ntheta:j + 2 * self.ntheta] = self.xi2_finalbins[:,Bin]

            if self.simple_approximation or self.full_marginalisation:
                for index in range(self.nfitparameters):
                    iz = 0
                    for Bin in range(self.nzcorrs):
                        iz = iz + 1  # this counts the bin combinations
                        j = (iz - 1) * 2 * self.ntheta
                        self.xi_prime[index,j:j + self.ntheta] = self.xi1_prime[index,:,Bin]
                        self.xi_prime[index,j + self.ntheta:j + 2 * self.ntheta] = self.xi2_prime[index,:,Bin]

            if self.full_marginalisation:
                for m in range(self.nfitparameters):
                    for n in range(self.nfitparameters):
                        iz = 0
                        for Bin in range(self.nzcorrs):
                            iz = iz + 1  # this counts the bin combinations
                            j = (iz - 1) * 2 * self.ntheta
                            self.xi_2prime[m,n,j:j + self.ntheta] = self.xi1_2prime[m,n,:,Bin]
                            self.xi_2prime[m,n,j + self.ntheta:j + 2 * self.ntheta] = self.xi2_2prime[m,n,:,Bin]
        ############
        # here we add the theta-dependent c-term function
        # it's zero if not requested!
        # same goes for constant relative offset of c-correction dc_sqr
        # TODO: in both arrays the xim-component is set to zero for now!!!
        #print self.xi, self.xi.shape
        #print xipm_c, xipm_c.shape
        #print dc_sqr, dc_sqr.shape

        # double check for sorting of self.__get_xi_p_and_xi_m:
        #xi_p_test, xi_m_test = self.__get_xi_p_and_xi_m(self.xi)
        #print np.allclose(xi_p_test - xi_p, 0.)
        #print np.allclose(xi_m_test - xi_m, 0.)

        # I guess this stuff will also affect the xi_prime and xi_2prime values...
        # Check this!!!
        self.xi = self.xi * dm_plus_one_sqr_obs + xipm_c + dc_sqr
        if self.write_out_theory:
            # write out masked theory vector in list format:
            self.__write_out_vector_in_list_format(self.xi, fname_prefix='THEORY_xi_pm')
            print('Aborting run now... \n Set flag "write_out_theory = False" for likelihood evaluations! \n')
            exit()

        vec_fiducial = self.xi_obs[self.mask_indices] - self.xi[self.mask_indices]
        if self.simple_approximation or self.full_marginalisation:
            vec_prime = self.xi_prime[:,self.mask_indices]
        if self.full_marginalisation:
            vec_2prime = self.xi_2prime[:,:,self.mask_indices]

        # this is for running smoothly with MultiNest
        # (in initial checking of prior space, there might occur weird solutions)
        if np.isinf(vec_fiducial).any() or np.isnan(vec_fiducial).any():
            chi2_fiducial = 2e12
            if self.simple_approximation:
                chi2 = 2e12
                return(-chi2/2.)
            if self.full_marginalisation:
                chi2 = 2e12
                return(-chi2/2.)
        else:
            # don't invert that matrix...
            # use the Cholesky decomposition instead:
            yt_fiducial = solve_triangular(self.cholesky_transform, vec_fiducial, lower=True)
            chi2_fiducial = yt_fiducial.dot(yt_fiducial)
            yt_prime = np.zeros((self.nfitparameters, vec_fiducial.shape[0]))
            if self.simple_approximation or self.full_marginalisation:
                if np.isinf(vec_prime).any() or np.isnan(vec_prime).any():
                    chi2 = 2e12
                    return(-chi2/2.)
                else:
                    for i in range(self.nfitparameters):
                        yt_prime[i,:] = solve_triangular(self.cholesky_transform, vec_prime[i,:], lower=True)
                    # Fill L_prime vector (calculation similar to fiducial one, but with xi_prime vector)
                    for i in range(self.nzbins*self.ngaussians):
                        self.L_prime[i] = yt_fiducial.dot(yt_prime[i]) + yt_prime[i].dot(yt_fiducial)
                    chi2_simple_approximation = np.dot(self.L_prime,np.dot(self.calibration_matrix,self.L_prime))
            if self.full_marginalisation:
                if np.isinf(vec_2prime).any() or np.isnan(vec_2prime).any() or np.isinf(vec_prime).any() or np.isnan(vec_prime).any():
                    chi2 = 2e12
                    return(-chi2/2.)
                yt_2prime = np.zeros((self.nfitparameters,self.nfitparameters,vec_fiducial.shape[0]))
                for i in range(self.nfitparameters):
                    for j in range(self.nfitparameters):
                        yt_2prime[i,j,:] = solve_triangular(self.cholesky_transform, vec_2prime[i,j,:], lower=True)
                for i in range(self.nfitparameters):
                    for j in range(self.nfitparameters):
                        self.L_2prime[i,j] = np.dot(yt_prime[i],yt_prime[j]) + np.dot(yt_prime[j],yt_prime[i]) + np.dot(yt_2prime[i,j],yt_fiducial) + np.dot(yt_fiducial,yt_2prime[i,j])
        if self.full_marginalisation:
            l,d,p = scipy.linalg.ldl(self.L_2prime, lower=True)
            l2,d2,p2 = scipy.linalg.ldl(self.L_2prime + 1/2 * np.matmul(self.L_2prime, np.matmul(self.calibration_matrix,self.L_2prime)), lower=True)
            y = np.linalg.solve(l, self.L_prime)
            y2 = np.linalg.solve(l2, self.L_prime)
            chi2_full_marg1 = -(np.dot(y, np.dot(np.linalg.inv(d),y)) - np.dot(y2, np.dot(np.linalg.inv(d2),y2)))/2
            # Two options:
            # 1) Tr(ln(1+\Sigma_cal * L''))
            # 2) ln(det(ln(1+\Sigma_cal * L''))
            # Option 2) is significantly faster.
            # chi2_full_marg2 = np.trace(scipy.linalg.logm(np.identity(self.nfitparameters) + np.matmul(self.calibration_matrix, self.L_2prime) / 2.))
            # sign, chi2_full_marg2 = np.linalg.slogdet(np.identity(self.nfitparameters) + np.matmul(self.calibration_matrix, self.L_2prime) / 2.)
            # Use SVD to calculate ln(det) because of numerical stability
            chi2_full_marg2 = np.sum(np.log(np.linalg.svd(np.identity(self.nfitparameters) + np.matmul(self.calibration_matrix, self.L_2prime)/2, compute_uv=False)))
            chi2 = chi2_fiducial + chi2_full_marg1 + chi2_full_marg2
            if chi2<0:
                chi2 = 2e24
                return(-chi2/2.)
        elif self.simple_approximation:
            chi2 = chi2_fiducial-chi2_simple_approximation / 4.
        else:
            chi2 = chi2_fiducial

        # enforce Gaussian priors on NUISANCE parameters if requested:
        if self.use_gaussian_prior_for_nuisance:

            for idx_nuisance, nuisance_name in enumerate(self.gaussian_prior_name):

                scale = data.mcmc_parameters[nuisance_name]['scale']
                chi2 += (data.mcmc_parameters[nuisance_name]['current'] * scale - self.gaussian_prior_center[idx_nuisance])**2 / self.gaussian_prior_sigma[idx_nuisance]**2

        #print chi2
        #dt = timer() - t0
        #print 'Time for one likelihood evaluation: {:.6f}s.'.format(dt)

        return -chi2 / 2.

    #######################################################################################################
    # This function is used to convert 2D sums over the two indices (Bin1, Bin2) of an N*N symmetric matrix
    # into 1D sums over one index with N(N+1)/2 possible values
    # An additional parameter n_components was added so that it can be used for arbitrary matrices
    def one_dim_index(self, Bin1, Bin2, n_components):
        if Bin1 <= Bin2:
            return int(Bin2 + n_components * Bin1 - (Bin1 * (Bin1 + 1)) / 2)
        else:
            return int(Bin1 + n_components * Bin2 - (Bin2 * (Bin2 + 1)) / 2)
    # This function converts a z-bin index and a comb component index into a 1D index
    def one_dim_index_L_vector(self, z_bin, comb_bin, n_components):
        if comb_bin>=n_components:
            raise Exception("Bin 2 > n_components!")
        return int(comb_bin + n_components * z_bin)
    # Redshift distribution of comb components (see eq.2 and eq.3)
    def K(self,z,z_mean,sigma):
        return(z*np.exp(-(z-z_mean)**2/(2*sigma**2))/(np.sqrt(np.pi/2)*z_mean*sigma*special.erfc(-z_mean/(np.sqrt(2)*sigma))+sigma**2*np.exp(-z_mean**2/(2*sigma**2))))
